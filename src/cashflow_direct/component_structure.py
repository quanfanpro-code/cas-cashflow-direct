"""凭证内业务关系与现金来源。关系限定候选，金额只验证，不反推业务事实。"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cashflow_direct.models import NormalizedEntry
from cashflow_direct.money import stable_id


@dataclass(frozen=True, slots=True)
class StructureEvidence:
    entry_id: str
    text: str
    relation: str


@dataclass(frozen=True, slots=True)
class StructureCashShare:
    entry_id: str
    amount_cent: int


@dataclass(frozen=True, slots=True)
class StructureBusinessGroup:
    group_id: str
    main_entry_ids: tuple[str, ...]
    settlement_entry_ids: tuple[str, ...]
    cash_shares: tuple[StructureCashShare, ...]
    evidence: tuple[StructureEvidence, ...]


@dataclass(frozen=True, slots=True)
class StructureNonCashGroup:
    group_id: str
    entry_ids: tuple[str, ...]
    evidence: tuple[StructureEvidence, ...]


@dataclass(frozen=True, slots=True)
class VoucherStructurePlan:
    plan_id: str
    voucher_key: str
    business_groups: tuple[StructureBusinessGroup, ...]
    noncash_groups: tuple[StructureNonCashGroup, ...]
    unresolved_entry_ids: tuple[str, ...]
    pending_cash_shares: tuple[StructureCashShare, ...]


@dataclass(frozen=True, slots=True)
class StructureSearchResult:
    plan: VoucherStructurePlan
    candidates: tuple[VoucherStructurePlan, ...]
    search_complete: bool
    reason: str


def _balance(row: NormalizedEntry) -> int:
    return row.debit_cent - row.credit_cent


def _has(value: str, terms: Sequence[str]) -> bool:
    return any(term in value for term in terms)


def _evidence(rows: Sequence[NormalizedEntry], relation: str) -> tuple[StructureEvidence, ...]:
    return tuple(StructureEvidence(row.entry_id, row.summary, relation) for row in rows)


def _plan(voucher: str, groups, noncash, rows, cash) -> VoucherStructurePlan:
    groups = tuple(sorted(groups, key=lambda g: g.group_id))
    used = {i for g in groups for i in (*g.main_entry_ids, *g.settlement_entry_ids)}
    used.update(i for g in noncash for i in g.entry_ids)
    allocated = defaultdict(int)
    for group in groups:
        for share in group.cash_shares:
            allocated[share.entry_id] += share.amount_cent
    pending = tuple(StructureCashShare(r.entry_id, _balance(r) - allocated[r.entry_id])
                    for r in cash if _balance(r) != allocated[r.entry_id])
    cash_ids = {r.entry_id for r in cash}
    unresolved = tuple(r.entry_id for r in rows if r.entry_id not in cash_ids and r.entry_id not in used)
    identity = repr((groups, noncash, unresolved, pending))
    return VoucherStructurePlan(stable_id("SP", voucher, identity), voucher, groups,
                                tuple(noncash), unresolved, pending)


def _business_groups(rows, cash, policy):
    """只表达可证明的现金份额；多来源、多性质不得任意顺序分摊。"""
    amount = sum(_balance(r) for r in cash)
    if not rows or not amount or sum(_balance(r) for r in rows) != -amount:
        return ()
    if any(_balance(r) * amount <= 0 for r in cash):
        return ()
    payroll = [r for r in rows if _has(r.account_name, policy["payroll_accounts"])]
    if payroll and all(_has(r.summary, policy["payroll_terms"]) for r in payroll):
        main = payroll
        offsets = [r for r in rows if r not in main]
        if (any(_balance(r) * amount >= 0 for r in main)
                or any(_balance(r) * amount <= 0 or not _has(r.account_name, policy["settlement_accounts"])
                       for r in offsets)):
            return ()
        parts = [(main, offsets)]
    elif all(_balance(r) * amount < 0 for r in rows):
        same_business = len({(r.summary, r.account_name) for r in rows}) == 1
        vat_companion = (len({r.summary for r in rows}) == 1
                         and any(_has(r.account_name, policy["vat_accounts"]) for r in rows))
        debt_interest = (any(_has(r.account_name, policy["principal_accounts"]) for r in rows)
                         and any(_has(r.account_name, policy["interest_accounts"]) for r in rows))
        if len(cash) == 1 and (same_business or vat_companion) and not debt_interest:
            parts = [(rows, [])]
        elif len(cash) == 1:
            parts = [([r], []) for r in rows]
        elif len({(r.summary, r.account_name) for r in rows}) == 1:
            parts = [(rows, [])]
        else:
            return ()
    else:
        return ()
    result = []
    for main, offsets in parts:
        part_amount = -sum(_balance(r) for r in (*main, *offsets))
        shares = ((StructureCashShare(cash[0].entry_id, part_amount),) if len(cash) == 1
                  else tuple(StructureCashShare(r.entry_id, _balance(r)) for r in cash))
        ids = tuple(r.entry_id for r in main)
        offset_ids = tuple(r.entry_id for r in offsets)
        evidence = []
        for row in (*main, *offsets):
            if row.summary in {r.summary for r in cash}:
                relation = "same_summary"
            elif re.search(str(policy["on_behalf_pattern"]), row.summary):
                relation = "on_behalf_same_payee"
            elif row in offsets:
                relation = "payroll_deduction_same_object"
            else:
                relation = "sole_cash_direction"
            evidence.append(StructureEvidence(row.entry_id, row.summary, relation))
        result.append(StructureBusinessGroup(stable_id("BG", *ids, *offset_ids, repr(shares)),
                      ids, offset_ids, shares, (*evidence, *_evidence(cash, "cash_source"))))
    return tuple(result)


def resolve_voucher_structure(entries: Sequence[NormalizedEntry], cash_entry_ids: Sequence[str],
                              policy: Mapping[str, object]) -> StructureSearchResult:
    rows = tuple(sorted(entries, key=lambda r: r.entry_id))
    cash_ids = set(cash_entry_ids)
    if (not rows or not cash_ids or len({r.entry_id for r in rows}) != len(rows)
            or not cash_ids.issubset({r.entry_id for r in rows})
            or len({r.voucher_key for r in rows}) != 1):
        raise ValueError("业务结构输入必须为同一凭证的唯一原始行和有效现金来源")
    cash = tuple(r for r in rows if r.entry_id in cash_ids)
    voucher = rows[0].voucher_key
    noncash = [r for r in rows if r.entry_id not in cash_ids]
    if len(noncash) > int(policy["max_relation_rows"]):
        plan = _plan(voucher, (), (), rows, cash)
        return StructureSearchResult(plan, (), False, "关系搜索未完成：超过分录行数上限")
    accrual_groups = defaultdict(list)
    for row in noncash:
        if (_has(row.summary, policy["accrual_terms"])
                and row.summary not in {r.summary for r in cash}):
            accrual_groups[row.summary].append(row)
    excluded = []
    for summary, group in sorted(accrual_groups.items()):
        pairs = policy["accrual_account_pairs"]
        if (len(group) >= 2 and sum(_balance(r) for r in group) == 0
                and all(_balance(r) for r in group)
                and any(all(_has(r.account_name, pair) for r in group)
                        and all(any(term in r.account_name for r in group) for term in pair)
                        for pair in pairs)):
            ids = tuple(r.entry_id for r in group)
            excluded.append(StructureNonCashGroup(stable_id("NC", *ids), ids, _evidence(group, "balanced_accrual")))
    excluded_ids = {i for g in excluded for i in g.entry_ids}
    noncash = [r for r in noncash if r.entry_id not in excluded_ids]
    cash_groups = defaultdict(list)
    for row in cash:
        cash_groups[(row.summary, 1 if _balance(row) > 0 else -1)].append(row)
    keys = sorted(cash_groups)
    edges = []
    for row in noncash:
        targets = []
        for index, (summary, direction) in enumerate(keys):
            exact = bool(summary) and row.summary == summary
            on_behalf = (bool(re.search(str(policy["on_behalf_pattern"]), row.summary))
                         and re.sub(str(policy["on_behalf_pattern"]), "", row.summary) == summary)
            deduction = re.search(str(policy["deduction_object_pattern"]), row.summary)
            payroll = bool(deduction and _has(summary, policy["payroll_terms"])
                           and deduction.group("object") in summary
                           and _has(row.account_name, policy["settlement_accounts"])
                           and _balance(row) * direction > 0)
            if exact or on_behalf or payroll:
                targets.append(index)
        edges.append(tuple(targets))
    # With one cash direction and no opposing business lines, every business line
    # necessarily settles that sole cash group. No amount subset is selected.
    if len(keys) == 1 and noncash and all(_balance(r) * keys[0][1] < 0 for r in noncash):
        edges = [(0,) for _ in noncash]
    unknown = any(not choices for choices in edges)
    limit = int(policy["max_search_states"])
    combinations = 1
    for choices in edges:
        combinations *= max(1, len(choices))
        if combinations > limit:
            break
    complete = combinations <= limit and len(noncash) <= int(policy["max_relation_rows"])
    candidates = []
    if not unknown and complete:
        # Enumerate every relation-permitted assignment, not minimum-length amount subsets.
        assignments = [()]
        for choices in edges:
            assignments = [(*prior, choice) for prior in assignments for choice in choices]
        for assignment in assignments:
            groups = []
            for index, key in enumerate(keys):
                members = [row for row, target in zip(noncash, assignment) if target == index]
                groups.extend(_business_groups(members, cash_groups[key], policy))
            candidate = _plan(voucher, groups, excluded, rows, cash)
            if not candidate.unresolved_entry_ids and not candidate.pending_cash_shares:
                candidates.append(candidate)
    candidates = tuple({p.plan_id: p for p in candidates}.values())
    if len(candidates) == 1:
        plan = candidates[0]
        reason = "业务关系允许的完整分配方案唯一且逐来源守恒"
    elif candidates:
        common = set(candidates[0].business_groups)
        for candidate in candidates[1:]:
            common.intersection_update(candidate.business_groups)
        plan = _plan(voucher, common, excluded, rows, cash)
        reason = "存在多个业务关系与金额均成立的完整分配方案"
    else:
        groups = []
        if not unknown and complete:
            # A closed group can continue only if no row has a competing edge into it.
            for index, key in enumerate(keys):
                if any(index in choices and len(choices) != 1 for choices in edges):
                    continue
                members = [r for r, choices in zip(noncash, edges) if choices == (index,)]
                groups.extend(_business_groups(members, cash_groups[key], policy))
        plan = _plan(voucher, groups, excluded, rows, cash)
        reason = ("关系搜索未完成，不能判定唯一" if not complete else
                  "存在无业务关联依据、金额不闭合或现金来源无法唯一分配的分录")
    errors = validate_structure_plan(plan, rows, cash_ids)
    if errors:
        raise ValueError("业务组成不变量失败：" + "；".join(errors))
    return StructureSearchResult(plan, candidates, complete, reason)


def validate_structure_plan(plan: VoucherStructurePlan, entries: Sequence[NormalizedEntry],
                            cash_entry_ids: Sequence[str]) -> tuple[str, ...]:
    by_id = {r.entry_id: r for r in entries}
    cash_ids = set(cash_entry_ids)
    errors = []
    used = []
    allocated = defaultdict(int)
    for group in plan.business_groups:
        ids = (*group.main_entry_ids, *group.settlement_entry_ids)
        used.extend(ids)
        if not group.main_entry_ids or not group.evidence:
            errors.append("业务组缺少主项或原文依据")
        if any(i not in by_id or i in cash_ids for i in ids):
            errors.append("业务行不存在或误用现金行")
            continue
        if sum(s.amount_cent for s in group.cash_shares) != -sum(_balance(by_id[i]) for i in ids):
            errors.append("业务金额不守恒")
        for share in group.cash_shares:
            allocated[share.entry_id] += share.amount_cent
            if share.entry_id not in cash_ids or share.amount_cent * _balance(by_id[share.entry_id]) <= 0:
                errors.append("现金来源不存在或方向错误")
    for group in plan.noncash_groups:
        used.extend(group.entry_ids)
        if (not group.evidence or any(i not in by_id or i in cash_ids for i in group.entry_ids)
                or sum(_balance(by_id[i]) for i in group.entry_ids if i in by_id)):
            errors.append("非现金处置无依据或不平衡")
    used.extend(plan.unresolved_entry_ids)
    if len(used) != len(set(used)) or set(used) != set(by_id) - cash_ids:
        errors.append("业务行处置重复或不完整")
    for share in plan.pending_cash_shares:
        allocated[share.entry_id] += share.amount_cent
        if share.entry_id not in cash_ids or share.amount_cent * _balance(by_id[share.entry_id]) <= 0:
            errors.append("待分配现金来源或方向错误")
    if set(allocated) != cash_ids or any(allocated[i] != _balance(by_id[i]) for i in cash_ids):
        errors.append("逐现金来源分配不守恒")
    for group in (*plan.business_groups, *plan.noncash_groups):
        for evidence in group.evidence:
            if evidence.entry_id not in by_id or evidence.text != by_id[evidence.entry_id].summary:
                errors.append("原文依据与来源不符")
    if len(by_id) != len(entries) or any(r.voucher_key != plan.voucher_key for r in entries):
        errors.append("重复行编号或跨凭证引用")
    return tuple(errors)
