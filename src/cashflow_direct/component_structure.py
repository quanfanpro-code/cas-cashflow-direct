"""凭证内业务关系与现金来源。关系限定候选，金额只验证，不反推业务事实。"""
from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cashflow_direct.evidence import split_account_levels
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


def _reciprocal_counterparts(row, other) -> bool:
    account = split_account_levels(row.account_name)
    counterpart = split_account_levels(row.counterpart_name)
    return bool(account and counterpart
                and split_account_levels(other.counterpart_name)[:1] == account[:1]
                and split_account_levels(other.account_name)[:1] == counterpart[:1])


def _reciprocal_coverage(rows, cash) -> bool:
    """同一业务组两侧均有明确对应；不同现金账户不必与每条业务行交叉匹配。"""
    return bool(rows and cash) and (
        all(any(_reciprocal_counterparts(row, source) for source in cash) for row in rows)
        and all(any(_reciprocal_counterparts(row, source) for row in rows) for source in cash)
    )


def _unique_reciprocal_pairs(rows) -> bool:
    return all(sum(_reciprocal_counterparts(row, other)
                   and _balance(other) == -_balance(row) != 0
                   for other in rows if other.entry_id != row.entry_id) == 1
               for row in rows)


def _evidence(rows: Sequence[NormalizedEntry], relation: str) -> tuple[StructureEvidence, ...]:
    return tuple(StructureEvidence(row.entry_id, row.summary, relation) for row in rows)


def _matches_account_pair(rows: Sequence[NormalizedEntry], pairs) -> bool:
    if not rows:
        return False
    for pair in pairs:
        if (len(pair) == 1 and all(_has(row.account_name, pair) for row in rows)):
            return True
        if (len(pair) > 1
                and all(_has(row.account_name, pair) for row in rows)
                and all(any(term in row.account_name for row in rows) for term in pair)):
            return True

    # A single accrual may debit several expense paths against one payable.
    # Permit their registered pairs together only when they share an account
    # anchor and every row belongs to one of those fully represented pairs.
    anchors = {term for pair in pairs if len(pair) > 1 for term in pair}
    for anchor in anchors:
        present_pairs = [
            pair for pair in pairs if len(pair) > 1 and anchor in pair
            and all(any(term in row.account_name for row in rows) for term in pair)
        ]
        if len(present_pairs) < 2:
            continue
        allowed_terms = {term for pair in present_pairs for term in pair}
        if all(any(term in row.account_name for term in allowed_terms) for row in rows):
            return True
    return False


def _known_noncash_group(rows: Sequence[NormalizedEntry], policy: Mapping[str, object],
                         cash_summaries: set[str], cash_business_accounts: set[str]) -> bool:
    summaries = {row.summary for row in rows}
    summary = next(iter(summaries), "")
    return (
        len(rows) >= 2
        and len(summaries) == 1
        and bool(summary)
        and summary not in cash_summaries
        and all(_balance(row) for row in rows)
        and sum(_balance(row) for row in rows) == 0
        and (_matches_account_pair(rows, policy["accrual_account_pairs"])
             or _unique_reciprocal_pairs(rows)
             or any(row.account_name in cash_business_accounts for row in rows))
    )


def _balanced_summary_plan(rows: Sequence[NormalizedEntry], cash_ids: set[str],
                           policy: Mapping[str, object]) -> VoucherStructurePlan | None:
    """摘要不同的整组先各自平衡；不按原始行序或金额子集猜分录。"""
    by_summary = defaultdict(list)
    for row in rows:
        if not row.summary:
            return None
        by_summary[row.summary].append(row)
    if len(by_summary) < 2 or any(len(group) < 2 or sum(map(_balance, group))
                                  for group in by_summary.values()):
        return None
    cash_summaries = {row.summary for row in rows if row.entry_id in cash_ids}
    cash_business_accounts = {row.account_name for row in rows
                              if row.entry_id not in cash_ids and row.summary in cash_summaries}
    business, noncash = [], []
    for group in by_summary.values():
        group_cash = [row for row in group if row.entry_id in cash_ids]
        group_noncash = [row for row in group if row.entry_id not in cash_ids]
        if group_cash:
            parts = _business_groups(group_noncash, group_cash, policy)
            if not parts:
                return None
            business.extend(parts)
        else:
            if not _known_noncash_group(group_noncash, policy, cash_summaries,
                                        cash_business_accounts):
                return None
            ids = tuple(row.entry_id for row in group_noncash)
            noncash.append(StructureNonCashGroup(
                stable_id("NC", *ids), ids,
                _evidence(group_noncash, "independent_balanced_summary"),
            ))
    plan = _plan(rows[0].voucher_key, business, noncash, rows,
                 [row for row in rows if row.entry_id in cash_ids])
    return plan if (not plan.unresolved_entry_ids and not plan.pending_cash_shares
                    and not validate_structure_plan(plan, rows, cash_ids)) else None


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


def plan_from_agent_proposal(entries: Sequence[NormalizedEntry], cash_entry_ids: Sequence[str],
                             proposal: Mapping[str, object]) -> VoucherStructurePlan:
    """用原始分录重建 Agent 提议；逐行覆盖和现金守恒由验证器把关。"""
    rows = tuple(entries)
    if not rows or len({row.voucher_key for row in rows}) != 1:
        raise ValueError("Agent 业务组成必须只包含同一凭证")
    by_id = {row.entry_id: row for row in rows}
    cash_ids = set(cash_entry_ids)
    if not isinstance(proposal, Mapping) or set(proposal) != {"business_groups", "noncash_groups"}:
        raise ValueError("Agent 业务组成字段不完整")
    groups = []
    for raw in proposal["business_groups"]:
        if not isinstance(raw, Mapping) or set(raw) != {"main_entry_ids", "settlement_entry_ids", "cash_shares"}:
            raise ValueError("Agent 现金业务组字段不完整")
        main = tuple(str(value) for value in raw["main_entry_ids"])
        settlement = tuple(str(value) for value in raw["settlement_entry_ids"])
        if not main or any(key not in by_id or key in cash_ids for key in (*main, *settlement)):
            raise ValueError("Agent 现金业务组主项或结算行无效")
        shares = []
        for item in raw["cash_shares"]:
            if (not isinstance(item, Mapping) or set(item) != {"entry_id", "amount_cent"}
                    or type(item["amount_cent"]) is not int):
                raise ValueError("Agent 现金来源必须提供原始行和整数分金额")
            shares.append(StructureCashShare(str(item["entry_id"]), item["amount_cent"]))
        if not shares:
            raise ValueError("Agent 现金业务组缺少现金来源")
        ids = (*main, *settlement)
        evidence = (*_evidence([by_id[key] for key in ids], "agent_original_entry"),
                    *_evidence([by_id[key] for key in cash_entry_ids if key in {s.entry_id for s in shares}],
                               "cash_source"))
        groups.append(StructureBusinessGroup(
            stable_id("BG", *ids, repr(tuple(shares))), main, settlement, tuple(shares), evidence,
        ))
    noncash = []
    for raw in proposal["noncash_groups"]:
        if not isinstance(raw, Mapping) or set(raw) != {"entry_ids"}:
            raise ValueError("Agent 非现金组字段不完整")
        ids = tuple(str(value) for value in raw["entry_ids"])
        if len(ids) < 2 or any(key not in by_id or key in cash_ids for key in ids):
            raise ValueError("Agent 非现金组来源行无效")
        noncash.append(StructureNonCashGroup(
            stable_id("NC", *ids), ids, _evidence([by_id[key] for key in ids], "agent_original_entry"),
        ))
    plan = _plan(rows[0].voucher_key, groups, noncash, rows,
                 [by_id[key] for key in cash_entry_ids if key in by_id])
    errors = validate_structure_plan(plan, rows, cash_entry_ids)
    if plan.unresolved_entry_ids or plan.pending_cash_shares or errors:
        raise ValueError("Agent 业务组成未覆盖原始行或现金来源，或金额不守恒：" + "；".join(errors))
    return plan


def _business_groups(rows, cash, policy):
    """只表达可证明的现金份额；多来源、多性质不得任意顺序分摊。"""
    amount = sum(_balance(r) for r in cash)
    if not rows or not amount or sum(_balance(r) for r in rows) != -amount:
        return ()
    if any(_balance(r) * amount <= 0 for r in cash):
        return ()
    payroll = [r for r in rows if _has(r.account_name, policy["payroll_accounts"])]
    if payroll and (all(_has(r.summary, policy["payroll_terms"]) for r in payroll)
                    or _reciprocal_coverage(payroll, cash)):
        main = payroll
        offsets = [r for r in rows if r not in main]
        if (any(_balance(r) * amount >= 0 for r in main)
                or any(not _has(r.account_name, policy["settlement_accounts"])
                       for r in offsets)):
            return ()
        parts = [(main, offsets)]
    elif (len(cash) == 1
          and len([r for r in rows if _balance(r) * amount < 0]) == 1
          and any(_balance(r) * amount > 0 for r in rows)
          and (next(r for r in rows if _balance(r) * amount < 0).summary == cash[0].summary
               or (len(cash[0].summary) >= 4 and next(
                   r for r in rows if _balance(r) * amount < 0
               ).summary.startswith(cash[0].summary)))
          and all(_has(r.account_name, policy["settlement_accounts"])
                  and r.summary.startswith(cash[0].summary)
                  for r in rows if _balance(r) * amount > 0)):
        main = [r for r in rows if _balance(r) * amount < 0]
        offsets = [r for r in rows if r not in main]
        parts = [(main, offsets)]
    elif all(_balance(r) * amount < 0 for r in rows):
        same_business = len({(r.summary, r.account_name) for r in rows}) == 1
        vat_companion = (len({r.summary for r in rows}) == 1
                         and any(_has(r.account_name, policy["vat_accounts"]) for r in rows))
        debt_interest = (any(_has(r.account_name, policy["principal_accounts"]) for r in rows)
                         and any(_has(r.account_name, policy["interest_accounts"]) for r in rows))
        if ((len(cash) == 1 or len({r.summary for r in (*rows, *cash)}) == 1)
                and (same_business or vat_companion) and not debt_interest):
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


def _equivalent_source_groups(business_rows, cash, policy, assigned):
    """仅来源全行相同时消除行号歧义，每笔业务和现金记录仍分别保留。"""
    source_classes = defaultdict(list)
    for source in cash:
        if not source.source_row_hash:
            return ()
        key = (source.source_row_hash, source.account_name, source.counterpart_name,
               source.summary, source.original_flow_item, _balance(source))
        source_classes[key].append(source)
    assignments = defaultdict(list)
    for key, members in business_rows.items():
        remaining = (sum(_balance(row) for row in members)
                     + sum(_balance(row) for row in assigned[key]))
        if not remaining:
            continue
        matching = [signature for signature, sources in source_classes.items()
                    if remaining == -_balance(sources[0])
                    and _reciprocal_coverage(members, sources)]
        if len(matching) != 1:
            return ()
        assignments[matching[0]].append(key)
    for signature, sources in source_classes.items():
        keys = sorted(assignments[signature])
        if len(keys) != len(sources):
            return ()
        for key, source in zip(keys, sorted(sources, key=lambda row: row.entry_id)):
            assigned[key].append(source)
    result = []
    for key, members in business_rows.items():
        sources = assigned[key]
        groups = _business_groups(members, sources, policy)
        if len(groups) != 1:
            return ()
        group = groups[0]
        result.append(StructureBusinessGroup(
            group.group_id, group.main_entry_ids, group.settlement_entry_ids,
            group.cash_shares,
            (*_evidence(members, "reciprocal_counterpart_exact_amount"),
             *_evidence(sources, "cash_source")),
        ))
    return tuple(result)


def _reciprocal_counterpart_groups(rows, cash, policy, *, allow_partial=False):
    """双方所填对方一级互相对应，且逐来源金额配对唯一时，采用显式分录关系。"""
    if not rows or not cash:
        return ()
    if ((len({(row.summary, row.account_name) for row in rows}) == 1
         or all(_has(row.account_name, policy["payroll_accounts"]) for row in rows))
            and _reciprocal_coverage(rows, cash)):
        groups = _business_groups(rows, cash, policy)
        cash_ids = {source.entry_id for source in cash}
        return tuple(StructureBusinessGroup(
            group.group_id, group.main_entry_ids, group.settlement_entry_ids,
            group.cash_shares,
            tuple(StructureEvidence(e.entry_id, e.text,
                  "cash_source" if e.entry_id in cash_ids else "reciprocal_counterpart_same_business")
                  for e in group.evidence),
        ) for group in groups)
    business_rows = defaultdict(list)
    for row in rows:
        key = (("explicit_staff_settlement", "")
               if _has(row.account_name, policy["payroll_accounts"])
               else (row.summary, row.account_name))
        business_rows[key].append(row)
    cash_by_business = defaultdict(list)
    pending_sources = list(cash)
    while pending_sources:
        progressed = False
        for source in tuple(pending_sources):
            matches = set()
            for key, members in business_rows.items():
                remaining = (sum(_balance(row) for row in members)
                             + sum(_balance(row) for row in cash_by_business[key]))
                if (remaining * _balance(source) < 0
                        and abs(remaining) >= abs(_balance(source))
                        and any(_reciprocal_counterparts(row, source)
                                and _balance(source) == -_balance(row) != 0
                                for row in members)):
                    matches.add(key)
            exact_summaries = {key for key in matches if key[0] == source.summary}
            if exact_summaries:
                matches = exact_summaries
            if len(matches) == 1:
                cash_by_business[next(iter(matches))].append(source)
                pending_sources.remove(source)
                progressed = True
        if not progressed:
            equivalent = _equivalent_source_groups(business_rows, pending_sources, policy, cash_by_business)
            if equivalent or not allow_partial:
                return equivalent
            break
    result = []
    for key, members in business_rows.items():
        sources = cash_by_business[key]
        groups = _business_groups(members, sources, policy)
        if len(groups) != 1:
            if allow_partial:
                continue
            return ()
        group = groups[0]
        result.append(StructureBusinessGroup(
            group.group_id, group.main_entry_ids, group.settlement_entry_ids,
            group.cash_shares,
            (*_evidence(members, "reciprocal_counterpart_exact_amount"),
             *_evidence(sources, "cash_source")),
        ))
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

    # 未提供分录号时，原始行序不能证明业务关系；仅抵消同科目、同金额的唯一借贷对。
    by_account_amount = defaultdict(lambda: {1: [], -1: []})
    for row in noncash:
        amount = _balance(row)
        if row.account_name and row.summary and amount:
            by_account_amount[(row.account_name, abs(amount))][1 if amount > 0 else -1].append(row)
    excluded = []
    explicit_ids = set()
    for sides in by_account_amount.values():
        if len(sides[1]) == len(sides[-1]) == 1:
            pair = (sides[1][0], sides[-1][0])
            ids = tuple(row.entry_id for row in pair)
            excluded.append(StructureNonCashGroup(
                stable_id("NC", *ids), ids,
                _evidence(pair, "same_account_opposite_equal"),
            ))
            explicit_ids.update(ids)
    remaining_after_offset = [row for row in noncash if row.entry_id not in explicit_ids]
    if excluded:
        direct = _business_groups(remaining_after_offset, cash, policy)
        if direct:
            plan = _plan(voucher, direct, excluded, rows, cash)
            if (not plan.unresolved_entry_ids and not plan.pending_cash_shares
                    and not validate_structure_plan(plan, rows, cash_ids)):
                return StructureSearchResult(
                    plan, (plan,), True,
                    "同科目等额反向分录抵消后，剩余业务与现金逐来源闭合",
                )
    excluded = []
    explicit_ids = set()

    summary_plan = _balanced_summary_plan(rows, cash_ids, policy)
    if summary_plan is not None:
        return StructureSearchResult(
            summary_plan, (summary_plan,), True,
            "不同摘要的整组各自借贷平衡、原始行完整覆盖并逐现金来源守恒",
        )

    # 所填对方明确互相指向非现金科目时，先保留这项独立非现金结算；
    # 任何一方指向实际现金一级时均不在此排除，避免丢掉真实现金业务。
    cash_level1s = {split_account_levels(row.account_name)[:1] for row in cash}
    explicit_noncash = defaultdict(list)
    for row in noncash:
        counterpart = split_account_levels(row.counterpart_name)[:1]
        if counterpart and counterpart not in cash_level1s and row.summary:
            explicit_noncash[row.summary].append(row)
    def exclude_balanced_explicit_groups():
        for candidates in explicit_noncash.values():
            group = tuple(row for row in candidates if row.entry_id not in explicit_ids)
            if (len(group) >= 2 and sum(_balance(row) for row in group) == 0
                    and all(any(_reciprocal_counterparts(row, other)
                                and _balance(row) * _balance(other) < 0
                                for other in group) for row in group)):
                ids = tuple(row.entry_id for row in group)
                excluded.append(StructureNonCashGroup(
                    stable_id("NC", *ids), ids,
                    _evidence(group, "reciprocal_counterpart_noncash"),
                ))
                explicit_ids.update(ids)

    exclude_balanced_explicit_groups()
    remaining_explicit = [row for group in explicit_noncash.values() for row in group
                          if row.entry_id not in explicit_ids]
    pair_matches = {
        row.entry_id: tuple(other for other in remaining_explicit
                            if other.entry_id != row.entry_id
                            and _reciprocal_counterparts(row, other)
                            and _balance(other) == -_balance(row) != 0)
        for row in remaining_explicit
    }
    for row in remaining_explicit:
        matches = pair_matches[row.entry_id]
        if row.entry_id in explicit_ids or len(matches) != 1:
            continue
        other = matches[0]
        if tuple(x.entry_id for x in pair_matches[other.entry_id]) != (row.entry_id,):
            continue
        pair = (row, other)
        ids = tuple(x.entry_id for x in pair)
        excluded.append(StructureNonCashGroup(
            stable_id("NC", *ids), ids,
            _evidence(pair, "reciprocal_counterpart_noncash"),
        ))
        explicit_ids.update(ids)
    # 唯一配对拆出后，再验证整个剩余同摘要组；不搜索金额子集或猜配重复行。
    exclude_balanced_explicit_groups()
    # 对方一级已双向指明非现金结算时，重复的等额记录整体保留，
    # 不猜测人员或发票间的逐行配对，也不将抵扣解释成现金收付。
    reciprocal_pools = defaultdict(list)
    for group in explicit_noncash.values():
        for row in group:
            if row.entry_id in explicit_ids or not _balance(row):
                continue
            account = split_account_levels(row.account_name)[:1]
            counterpart = split_account_levels(row.counterpart_name)[:1]
            reciprocal_pools[(tuple(sorted((account, counterpart))), abs(_balance(row)))].append(row)
    for group in reciprocal_pools.values():
        if (len(group) >= 2 and sum(_balance(row) for row in group) == 0
                and all(any(_reciprocal_counterparts(row, other)
                            and _balance(other) == -_balance(row)
                            for other in group) for row in group)):
            ids = tuple(row.entry_id for row in group)
            excluded.append(StructureNonCashGroup(
                stable_id("NC", *ids), ids,
                _evidence(group, "reciprocal_counterpart_noncash"),
            ))
            explicit_ids.update(ids)
    noncash = [row for row in noncash if row.entry_id not in explicit_ids]
    accrual_groups = defaultdict(list)
    for row in noncash:
        if (_has(row.summary, policy["accrual_terms"])
                and row.summary not in {r.summary for r in cash}):
            accrual_groups[row.summary].append(row)
    cash_summaries = {row.summary for row in cash}
    cash_business_accounts = {row.account_name for row in noncash if row.summary in cash_summaries}
    for summary, group in sorted(accrual_groups.items()):
        if _known_noncash_group(group, policy, cash_summaries, cash_business_accounts):
            ids = tuple(r.entry_id for r in group)
            excluded.append(StructureNonCashGroup(stable_id("NC", *ids), ids, _evidence(group, "balanced_accrual")))
    excluded_ids = {i for g in excluded for i in g.entry_ids}
    noncash = [r for r in noncash if r.entry_id not in excluded_ids]
    shortest_cash_summary = min(cash_summaries, key=len)
    if (noncash and cash and not any(row.counterpart_name for row in (*noncash, *cash))
            and all(row.summary.startswith(shortest_cash_summary) for row in cash)
            and all(_has(row.summary, policy["payroll_terms"]) for row in cash)
            and any(_has(row.account_name, policy["payroll_accounts"]) for row in noncash)):
        direct = _business_groups(noncash, cash, policy)
        if direct:
            plan = _plan(voucher, direct, excluded, rows, cash)
            if (not plan.unresolved_entry_ids and not plan.pending_cash_shares
                    and not validate_structure_plan(plan, rows, cash_ids)):
                return StructureSearchResult(
                    plan, (plan,), True, "薪酬主项、代扣结算与全部现金来源完整闭合",
                )
    if len(cash) == 1 and noncash:
        direct = _business_groups(noncash, cash, policy)
        if len(direct) == 1 and direct[0].settlement_entry_ids:
            plan = _plan(voucher, direct, excluded, rows, cash)
            if (not plan.unresolved_entry_ids and not plan.pending_cash_shares
                    and not validate_structure_plan(plan, rows, cash_ids)):
                return StructureSearchResult(
                    plan, (plan,), True, "同摘要结算主项及抵付行完整闭合",
                )
    reciprocal_groups = _reciprocal_counterpart_groups(noncash, cash, policy, allow_partial=True)
    if reciprocal_groups:
        reciprocal_plan = _plan(voucher, reciprocal_groups, excluded, rows, cash)
        if not validate_structure_plan(reciprocal_plan, rows, cash_ids):
            if reciprocal_plan.unresolved_entry_ids or reciprocal_plan.pending_cash_shares:
                return StructureSearchResult(
                    reciprocal_plan, (), True,
                    "独立闭合且双方对应的业务继续；其余分录与现金来源仍待核实",
                )
            return StructureSearchResult(
                reciprocal_plan, (reciprocal_plan,), True,
                "双方对方一级互相对应，逐现金来源金额配对唯一且守恒",
            )
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
