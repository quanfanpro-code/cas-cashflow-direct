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
    grouped: dict[str, list[NormalizedEntry]] = defaultdict(list)
    for entry in entries:
        if _looks_like_cash(entry.account_name):
            grouped[_account_key(entry.account_name)].append(entry)
    candidates: list[CashScopeCandidate] = []
    for key, items in sorted(grouped.items()):
        names = tuple(sorted({item.account_name for item in items}))
        debit = sum(item.debit_cent for item in items)
        credit = sum(item.credit_cent for item in items)
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


def _signed_flow(entry: NormalizedEntry) -> int:
    if entry.retained_side == "unknown" and entry.flow_amount_cent and entry.original_flow_item:
        if any(term in entry.original_flow_item for term in OUTFLOW_ITEM_TERMS):
            return -entry.flow_amount_cent
        if any(term in entry.original_flow_item for term in INFLOW_ITEM_TERMS):
            return entry.flow_amount_cent
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
    return _component(
        entry.voucher_key,
        1,
        amount,
        entry.summary,
        counterpart,
        entry.original_flow_item,
        (entry.entry_id,),
        ("one_sided_source",),
        "weak",
    )


def _match_internal_transfers(
    voucher_key: str,
    cash_entries: list[NormalizedEntry],
) -> tuple[dict[str, int], list[InternalTransferLeg]]:
    remaining = {entry.entry_id: _signed_flow(entry) for entry in cash_entries}
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
    remaining, excluded = _match_internal_transfers(voucher_key, cash_entries)
    external_delta = sum(remaining.values())
    if external_delta == 0:
        return [], excluded

    noncash = [entry for entry in entries if entry not in cash_entries]
    imbalance = sum(entry.debit_cent - entry.credit_cent for entry in entries)
    base_anomalies = ("voucher_unbalanced",) if imbalance else ()
    counterpart_accounts = tuple(dict.fromkeys(entry.account_name for entry in noncash if entry.account_name))
    cash_source_keys = tuple(entry.entry_id for entry in cash_entries if remaining[entry.entry_id])

    direction = 1 if external_delta > 0 else -1
    opposite_labeled = [
        entry
        for entry in noncash
        if entry.original_flow_item and (entry.debit_cent - entry.credit_cent) * direction < 0
    ]
    labeled = opposite_labeled or [entry for entry in cash_entries if entry.original_flow_item]
    by_item: dict[str, list[NormalizedEntry]] = defaultdict(list)
    for entry in labeled:
        by_item[entry.original_flow_item].append(entry)

    if len(by_item) <= 1:
        item = next(iter(by_item), next((entry.original_flow_item for entry in entries if entry.original_flow_item), ""))
        sources = cash_source_keys + tuple(entry.entry_id for entry in by_item.get(item, ()))
        return [
            _component(
                voucher_key,
                1,
                external_delta,
                entries[0].summary,
                counterpart_accounts,
                item,
                sources,
                base_anomalies,
                "strong" if noncash else "weak",
            )
        ], excluded

    components: list[CashflowComponent] = []
    allocated = 0
    for sequence, (item, item_entries) in enumerate(by_item.items(), 1):
        item_amount = sum(abs(entry.debit_cent - entry.credit_cent) for entry in item_entries)
        amount = direction * min(item_amount, max(0, abs(external_delta) - abs(allocated)))
        if amount == 0:
            continue
        allocated += amount
        item_accounts = tuple(dict.fromkeys(entry.account_name for entry in item_entries if entry.account_name))
        components.append(
            _component(
                voucher_key,
                sequence,
                amount,
                entries[0].summary,
                item_accounts,
                item,
                cash_source_keys + tuple(entry.entry_id for entry in item_entries),
                base_anomalies,
                "strong",
            )
        )
    residual = external_delta - allocated
    if residual:
        components.append(
            _component(
                voucher_key,
                len(components) + 1,
                residual,
                entries[0].summary,
                counterpart_accounts,
                "",
                cash_source_keys,
                base_anomalies + ("unallocated_cash",),
                "weak",
            )
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
            built, internal = _voucher_components(voucher_key, voucher_entries, cash_entries)
            components.extend(built)
            excluded.extend(internal)
            continue
        for entry in voucher_entries:
            if entry.flow_amount_cent:
                components.append(_build_one_sided(entry))
    return ComponentBuildResult(tuple(components), tuple(excluded), 0)
