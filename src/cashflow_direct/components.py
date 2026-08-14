from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from cashflow_direct.models import CashflowComponent, NormalizedEntry
from cashflow_direct.money import stable_id


CASH_TERMS = ("库存现金", "银行存款", "其他货币资金", "现金等价物")
RESTRICTED_TERMS = ("受限", "冻结", "保证金", "质押", "监管")
INFLOW_ITEM_TERMS = ("收到", "收回", "取得借款", "吸收投资", "销售商品")
OUTFLOW_ITEM_TERMS = ("支付", "购买", "购建", "偿还", "分配股利")


@dataclass(frozen=True, slots=True)
class CashScopeCandidate:
    account_key: str
    account_names: tuple[str, ...]
    debit_cent: int
    credit_cent: int
    net_change_cent: int
    restricted_terms: tuple[str, ...]
    system_suggestion: str


@dataclass(frozen=True, slots=True)
class CashScopeProposal:
    candidates: tuple[CashScopeCandidate, ...]


@dataclass(frozen=True, slots=True)
class CashScope:
    included_keys: frozenset[str]
    excluded_keys: frozenset[str]
    account_names_by_key: tuple[tuple[str, tuple[str, ...]], ...]
    scope_hash: str


@dataclass(frozen=True, slots=True)
class InternalTransferLeg:
    voucher_key: str
    entry_id: str
    matched_cent: int


@dataclass(frozen=True, slots=True)
class ComponentBuildResult:
    components: tuple[CashflowComponent, ...]
    excluded_internal_transfers: tuple[InternalTransferLeg, ...]
    unresolved_cash_delta_cent: int = 0


def _account_key(account_name: str) -> str:
    match = re.match(r"\s*(\d{4,})", account_name)
    return match.group(1) if match else "".join(account_name.split())


def _looks_like_cash(account_name: str) -> bool:
    return bool(account_name) and any(term in account_name for term in CASH_TERMS + RESTRICTED_TERMS)


def discover_cash_scope(entries: Sequence[NormalizedEntry]) -> CashScopeProposal:
    grouped: dict[str, list[tuple[NormalizedEntry, str, int, int]]] = defaultdict(list)
    for entry in entries:
        if _looks_like_cash(entry.account_name):
            grouped[_account_key(entry.account_name)].append(
                (entry, entry.account_name, entry.debit_cent, entry.credit_cent)
            )
        elif entry.retained_side == "counterpart" and _looks_like_cash(entry.counterpart_name):
            grouped[_account_key(entry.counterpart_name)].append(
                (entry, entry.counterpart_name, entry.credit_cent, entry.debit_cent)
            )
    candidates: list[CashScopeCandidate] = []
    for key, items in sorted(grouped.items()):
        names = tuple(sorted({name for _, name, _, _ in items}))
        debit = sum(item_debit for _, _, item_debit, _ in items)
        credit = sum(item_credit for _, _, _, item_credit in items)
        restrictions = tuple(
            term for term in RESTRICTED_TERMS if any(term in name for name in names)
        )
        suggestion = "confirm" if restrictions else "include"
        candidates.append(
            CashScopeCandidate(key, names, debit, credit, debit - credit, restrictions, suggestion)
        )
    return CashScopeProposal(tuple(candidates))


def confirm_cash_scope(
    proposal: CashScopeProposal,
    decisions: Mapping[str, str],
) -> CashScope:
    expected = {candidate.account_key for candidate in proposal.candidates}
    if set(decisions) != expected or any(value not in {"include", "exclude"} for value in decisions.values()):
        raise ValueError("等待现金范围确认：每个候选均需选择 include 或 exclude")
    included = frozenset(key for key, value in decisions.items() if value == "include")
    excluded = frozenset(key for key, value in decisions.items() if value == "exclude")
    names = tuple((candidate.account_key, candidate.account_names) for candidate in proposal.candidates)
    source = "|".join(f"{key}:{decisions[key]}" for key in sorted(expected))
    scope_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return CashScope(included, excluded, names, scope_hash)


def _keyword_direction(item: str) -> int | None:
    """标准项目名定收付方向：流出 -1，流入 +1，认不出 None。"""
    if any(term in item for term in OUTFLOW_ITEM_TERMS):
        return -1
    if any(term in item for term in INFLOW_ITEM_TERMS):
        return 1
    return None


def flow_direction_source(entry: NormalizedEntry) -> str:
    """该行现金方向的判定依据（写入留痕）。"""
    if entry.flow_amount_cent and entry.original_flow_item:
        direction = _keyword_direction(entry.original_flow_item)
        if direction == -1:
            return "现流项目名(流出)"
        if direction == 1:
            return "现流项目名(流入)"
    if entry.flow_amount_cent:
        return "借贷列+流量金额"
    return "借贷差额"


def _signed_flow(entry: NormalizedEntry) -> int:
    if entry.flow_amount_cent and entry.original_flow_item:
        direction = _keyword_direction(entry.original_flow_item)
        if direction is not None:
            return direction * entry.flow_amount_cent
    if entry.flow_amount_cent:
        if entry.debit_cent and not entry.credit_cent:
            side_delta = entry.flow_amount_cent
        elif entry.credit_cent and not entry.debit_cent:
            side_delta = -entry.flow_amount_cent
        else:
            side_delta = entry.flow_amount_cent
    else:
        side_delta = entry.debit_cent - entry.credit_cent
    return -side_delta if entry.retained_side == "counterpart" else side_delta


def _component(
    voucher_key: str,
    sequence: int,
    amount: int,
    summary: str,
    counterpart_accounts: tuple[str, ...],
    item: str,
    source_keys: tuple[str, ...],
    anomalies: tuple[str, ...],
    evidence_strength: str,
) -> CashflowComponent:
    return CashflowComponent(
        component_id=stable_id("CMP", voucher_key, sequence, item, amount, *source_keys),
        voucher_key=voucher_key,
        summary=summary,
        cash_delta_cent=amount,
        counterpart_accounts=counterpart_accounts,
        original_item_text=item,
        source_keys=tuple(dict.fromkeys(source_keys)),
        anomalies=anomalies,
        evidence_strength=evidence_strength,
    )


def _build_one_sided(entry: NormalizedEntry) -> CashflowComponent:
    amount = _signed_flow(entry)
    counterpart = (entry.account_name,) if entry.retained_side == "counterpart" and entry.account_name else ()
    usable_counterpart = entry.retained_side == "counterpart" and bool(entry.account_name)
    return _component(
        entry.voucher_key,
        1,
        amount,
        entry.summary,
        counterpart,
        entry.original_flow_item,
        (entry.entry_id,),
        () if usable_counterpart else ("one_sided_source",),
        "medium" if usable_counterpart else "weak",
    )


def _match_internal_transfers(
    voucher_key: str,
    cash_entries: list[NormalizedEntry],
    external_amounts: Sequence[int] = (),
) -> tuple[dict[str, int], list[InternalTransferLeg]]:
    remaining = {entry.entry_id: _signed_flow(entry) for entry in cash_entries}
    for external in external_amounts:
        candidates = (
            [entry for entry in cash_entries if remaining[entry.entry_id] > 0]
            if external > 0
            else [entry for entry in cash_entries if remaining[entry.entry_id] < 0]
        )
        amount_left = abs(external)
        for entry in candidates:
            available = abs(remaining[entry.entry_id])
            used = min(available, amount_left)
            remaining[entry.entry_id] -= used if external > 0 else -used
            amount_left -= used
            if amount_left == 0:
                break
    positives = [entry for entry in cash_entries if remaining[entry.entry_id] > 0]
    negatives = [entry for entry in cash_entries if remaining[entry.entry_id] < 0]
    excluded: list[InternalTransferLeg] = []
    for outgoing in negatives:
        for incoming in positives:
            if outgoing.account_name == incoming.account_name:
                continue
            amount = min(-remaining[outgoing.entry_id], remaining[incoming.entry_id])
            if amount <= 0:
                continue
            remaining[outgoing.entry_id] += amount
            remaining[incoming.entry_id] -= amount
            excluded.extend(
                (
                    InternalTransferLeg(voucher_key, outgoing.entry_id, amount),
                    InternalTransferLeg(voucher_key, incoming.entry_id, amount),
                )
            )
            if remaining[outgoing.entry_id] == 0:
                break
    return remaining, excluded


def _voucher_components(
    voucher_key: str,
    entries: list[NormalizedEntry],
    cash_entries: list[NormalizedEntry],
) -> tuple[list[CashflowComponent], list[InternalTransferLeg]]:
    noncash = [entry for entry in entries if entry not in cash_entries]
    cash_delta = sum(_signed_flow(entry) for entry in cash_entries)
    imbalance = sum(entry.debit_cent - entry.credit_cent for entry in entries)
    base_anomalies = ("voucher_unbalanced",) if imbalance else ()
    counterpart_accounts = tuple(dict.fromkeys(entry.account_name for entry in noncash if entry.account_name))
    labeled = [entry for entry in noncash if entry.original_flow_item]
    by_item: dict[str, list[NormalizedEntry]] = defaultdict(list)
    for entry in labeled:
        direction = "in" if _signed_flow(entry) > 0 else "out"
        by_item[f"{direction}\x1f{entry.original_flow_item}"].append(entry)
    principal_and_interest = (
        any(any(term in entry.account_name for term in ("借款", "债务")) for entry in noncash)
        and any("利息" in entry.account_name for entry in noncash)
    )
    if not by_item and principal_and_interest:
        for entry in noncash:
            direction = "in" if _signed_flow(entry) > 0 else "out"
            by_item[f"{direction}\x1f{entry.account_name}"].append(entry)

    if len(by_item) <= 1:
        item_entries = next(iter(by_item.values()), [])
        item = item_entries[0].original_flow_item if item_entries else next(
            (entry.original_flow_item for entry in cash_entries if entry.original_flow_item), ""
        )
        remaining, excluded = _match_internal_transfers(voucher_key, cash_entries, (cash_delta,))
        if cash_delta == 0:
            return [], excluded
        return [
            _component(
                voucher_key,
                1,
                cash_delta,
                entries[0].summary,
                counterpart_accounts,
                item,
                tuple(entry.entry_id for entry in cash_entries) + tuple(
                    entry.entry_id for entry in item_entries
                ),
                base_anomalies,
                "strong" if noncash else "weak",
            )
        ], excluded

    components: list[CashflowComponent] = []
    for sequence, item_entries in enumerate(by_item.values(), 1):
        item = item_entries[0].original_flow_item
        amount = sum(_signed_flow(entry) for entry in item_entries)
        item_accounts = tuple(dict.fromkeys(entry.account_name for entry in item_entries if entry.account_name))
        components.append(
            _component(
                voucher_key,
                sequence,
                amount,
                entries[0].summary,
                item_accounts,
                item,
                tuple(entry.entry_id for entry in cash_entries)
                + tuple(entry.entry_id for entry in item_entries),
                base_anomalies,
                "strong",
            )
        )
    allocated = sum(item.cash_delta_cent for item in components)
    residual = cash_delta - allocated
    if residual:
        components.append(
            _component(
                voucher_key,
                len(components) + 1,
                residual,
                entries[0].summary,
                counterpart_accounts,
                "",
                tuple(entry.entry_id for entry in cash_entries),
                base_anomalies + ("unallocated_cash",),
                "weak",
            )
        )
    remaining, excluded = _match_internal_transfers(
        voucher_key, cash_entries, tuple(item.cash_delta_cent for item in components)
    )
    if sum(remaining.values()):
        components[-1] = _component(
            voucher_key,
            len(components),
            components[-1].cash_delta_cent,
            components[-1].summary,
            components[-1].counterpart_accounts,
            components[-1].original_item_text,
            components[-1].source_keys,
            tuple(dict.fromkeys(components[-1].anomalies + ("cash_allocation_mismatch",))),
            "weak",
        )
    return components, excluded


def build_cashflow_components(
    entries: Sequence[NormalizedEntry],
    scope: CashScope,
) -> ComponentBuildResult:
    """按凭证构建现金流业务组成，并保持现金腿金额不变量。"""
    grouped: dict[str, list[NormalizedEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.voucher_key].append(entry)

    components: list[CashflowComponent] = []
    excluded: list[InternalTransferLeg] = []
    for voucher_key, voucher_entries in grouped.items():
        cash_entries = [
            entry
            for entry in voucher_entries
            if entry.account_name and _account_key(entry.account_name) in scope.included_keys
        ]
        if cash_entries:
            explicit_internal = [
                entry
                for entry in cash_entries
                if entry.counterpart_name
                and _account_key(entry.counterpart_name) in scope.included_keys
            ]
            if explicit_internal:
                excluded.extend(
                    InternalTransferLeg(voucher_key, entry.entry_id, abs(_signed_flow(entry)))
                    for entry in explicit_internal
                )
                cash_entries = [entry for entry in cash_entries if entry not in explicit_internal]
            if not cash_entries:
                continue
            built, internal = _voucher_components(voucher_key, voucher_entries, cash_entries)
            components.extend(built)
            excluded.extend(internal)
            continue
        for entry in voucher_entries:
            named_cash_counterpart = bool(
                entry.counterpart_name
                and _account_key(entry.counterpart_name) in scope.included_keys
            )
            if entry.flow_amount_cent or (
                entry.retained_side == "counterpart"
                and named_cash_counterpart
                and (entry.debit_cent or entry.credit_cent)
            ):
                components.append(_build_one_sided(entry))
    return ComponentBuildResult(tuple(components), tuple(excluded), 0)
