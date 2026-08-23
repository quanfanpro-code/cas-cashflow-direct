from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from cashflow_direct.models import CashflowComponent, NormalizedEntry
from cashflow_direct.money import stable_id


CASH_TERMS = ("库存现金", "银行存款", "其他货币资金", "现金等价物")
CASH_EQUIVALENT_TERMS = (
    "定期存款",
    "短期债券",
    "三个月内到期",
    "交易性金融资产",
    "债权投资",
    "理财",
)
RESTRICTED_TERMS = ("受限", "冻结", "保证金", "质押", "监管")
PERIOD_CHANGE_TERMS = ("质押", "解除质押", "冻结", "解除冻结")
INFLOW_ITEM_TERMS = ("收到", "收回", "取得借款", "吸收投资", "销售商品")
OUTFLOW_ITEM_TERMS = ("支付", "购买", "购建", "偿还", "分配股利")
# 非现金事项判定词条（保守：拿不准的不标 non_cash，宁可进 AI 复核）
NONCASH_SUMMARY_TERMS = ("计提",)
MAX_AMOUNT_COMBINATION_ROWS = 64
MAX_AMOUNT_COMBINATION_STATES = 50_000


@dataclass(frozen=True, slots=True)
class CashScopeCandidate:
    account_key: str
    account_names: tuple[str, ...]
    debit_cent: int
    credit_cent: int
    net_change_cent: int
    restricted_terms: tuple[str, ...]
    system_suggestion: str
    cash_equivalent_terms: tuple[str, ...] = ()
    period_change_terms: tuple[str, ...] = ()


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
class RoughReconciliation:
    applicable: bool
    status: str
    detail_sum_cent: int | None
    expected_cent: int | None
    difference_cent: int | None
    source_file_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ComponentBuildResult:
    components: tuple[CashflowComponent, ...]
    excluded_internal_transfers: tuple[InternalTransferLeg, ...]
    unresolved_cash_delta_cent: int = 0
    source_allocations: tuple[ComponentSourceAllocation, ...] = ()
    structure_requests: tuple[ComponentStructureRequest, ...] = ()


@dataclass(frozen=True, slots=True)
class ComponentStructureRequest:
    voucher_key: str
    cash_delta_cent: int
    candidate_entry_id_combinations: tuple[tuple[str, ...], ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ComponentSourceAllocation:
    component_id: str
    entry_id: str
    allocated_cent: int


@dataclass(frozen=True, slots=True)
class CashRowCleanupRequest:
    """无法可靠定位现金腿时，交给用户清洗输入的最小定位信息。"""

    voucher_key: str
    entry_ids: tuple[str, ...]
    reason: str


def _account_key(account_name: str) -> str:
    match = re.match(r"\s*(\d{4,})", account_name)
    return match.group(1) if match else "".join(account_name.split())


def account_key_for_entry(
    entry: NormalizedEntry, *, counterpart: bool = False
) -> str:
    if not counterpart and entry.account_code.strip():
        return "".join(entry.account_code.split())
    name = entry.counterpart_name if counterpart else entry.account_name
    return _account_key(name)


def find_cash_row_cleanup_requests(
    entries: Sequence[NormalizedEntry], scope: CashScope
) -> tuple[CashRowCleanupRequest, ...]:
    """找出带现金流痕迹、但无法确认哪一行是现金分录的凭证。"""
    grouped: dict[str, list[NormalizedEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.voucher_key].append(entry)

    requests: list[CashRowCleanupRequest] = []
    for voucher_key, voucher_entries in grouped.items():
        has_included_cash = any(
            entry.account_name
            and account_key_for_entry(entry) in scope.included_keys
            for entry in voucher_entries
        )
        has_named_included_cash = any(
            entry.counterpart_name
            and account_key_for_entry(entry, counterpart=True) in scope.included_keys
            for entry in voucher_entries
        )
        if has_included_cash or has_named_included_cash:
            continue
        has_confirmed_excluded_cash = any(
            entry.account_name
            and account_key_for_entry(entry) in scope.excluded_keys
            for entry in voucher_entries
        )
        if has_confirmed_excluded_cash:
            # 整张凭证只有已排除现金范围的账户，不构成现金及现金等价物流量。
            continue

        ambiguous: list[NormalizedEntry] = []
        for entry in voucher_entries:
            account_key = account_key_for_entry(entry) if entry.account_name else ""
            confirmed_excluded_cash = bool(account_key and account_key in scope.excluded_keys)
            has_flow_evidence = bool(
                entry.original_flow_item.strip() or entry.flow_amount_cent
            )
            structurally_marked_cash = entry.retained_side == "cash"
            if (has_flow_evidence or structurally_marked_cash) and not confirmed_excluded_cash:
                ambiguous.append(entry)
        if ambiguous:
            requests.append(
                CashRowCleanupRequest(
                    voucher_key=voucher_key,
                    entry_ids=tuple(entry.entry_id for entry in ambiguous),
                    reason=(
                        "存在现流项目、现流金额或现金行标记，但找不到已确认范围内的现金账户行，"
                        "也没有明确指向该现金账户的对方科目"
                    ),
                )
            )
    return tuple(requests)


def _account_identity(entry: NormalizedEntry) -> str:
    return (
        "".join(entry.account_code.split())
        if entry.account_code.strip()
        else "".join(entry.account_name.split())
    )


def _looks_like_cash(account_name: str) -> bool:
    return bool(account_name) and any(term in account_name for term in CASH_TERMS)


def _looks_like_cash_scope_candidate(account_name: str) -> bool:
    return bool(account_name) and any(
        term in account_name for term in (*CASH_TERMS, *CASH_EQUIVALENT_TERMS)
    )


def discover_cash_scope(entries: Sequence[NormalizedEntry]) -> CashScopeProposal:
    grouped: dict[str, list[tuple[NormalizedEntry, str, int, int]]] = defaultdict(list)
    for entry in entries:
        if _looks_like_cash_scope_candidate(entry.account_name):
            grouped[account_key_for_entry(entry)].append(
                (
                    entry,
                    entry.original_account_name or entry.account_name,
                    entry.debit_cent,
                    entry.credit_cent,
                )
            )
        elif entry.retained_side == "counterpart" and _looks_like_cash_scope_candidate(entry.counterpart_name):
            grouped[account_key_for_entry(entry, counterpart=True)].append(
                (
                    entry,
                    entry.original_counterpart_name or entry.counterpart_name,
                    entry.credit_cent,
                    entry.debit_cent,
                )
            )
    candidates: list[CashScopeCandidate] = []
    for key, items in sorted(grouped.items()):
        names = tuple(sorted({name for _, name, _, _ in items}))
        debit = sum(item_debit for _, _, item_debit, _ in items)
        credit = sum(item_credit for _, _, _, item_credit in items)
        restrictions = tuple(
            term for term in RESTRICTED_TERMS if any(term in name for name in names)
        )
        cash_equivalent_terms = tuple(
            term for term in CASH_EQUIVALENT_TERMS if any(term in name for name in names)
        )
        period_change_terms = tuple(
            term
            for term in PERIOD_CHANGE_TERMS
            if any(term in entry.summary for entry, _, _, _ in items)
        )
        suggestion = (
            "clarify_period_change"
            if period_change_terms
            else "confirm_cash_equivalent"
            if cash_equivalent_terms
            else "confirm"
            if restrictions
            else "include"
        )
        candidates.append(
            CashScopeCandidate(
                key,
                names,
                debit,
                credit,
                debit - credit,
                restrictions,
                suggestion,
                cash_equivalent_terms,
                period_change_terms,
            )
        )
    return CashScopeProposal(tuple(candidates))


def confirm_cash_scope(
    proposal: CashScopeProposal,
    decisions: Mapping[str, object],
) -> CashScope:
    expected = {candidate.account_key for candidate in proposal.candidates}
    if set(decisions) != expected:
        raise ValueError("等待现金范围确认：每个候选均需选择 include 或 exclude")
    candidate_by_key = {candidate.account_key: candidate for candidate in proposal.candidates}
    normalized: dict[str, str] = {}
    normalized_details: list[str] = []
    for key, raw in decisions.items():
        candidate = candidate_by_key[key]
        detail = raw if isinstance(raw, Mapping) else {}
        status = str(detail.get("status", "")) if detail else str(raw)
        if status not in {"include", "exclude"}:
            raise ValueError("等待现金范围确认：每个候选均需选择 include 或 exclude")
        if candidate.period_change_terms and status == "include":
            raise ValueError("现金范围在期间内发生变化，请按期间拆分账户或明细后重新确认")
        if candidate.cash_equivalent_terms and status == "include":
            criteria = (
                "short_term",
                "high_liquidity",
                "known_cash_amount",
                "low_value_change_risk",
                "available_for_payment",
            )
            if not detail or not all(detail.get(name) is True for name in criteria):
                raise ValueError("纳入现金等价物前必须确认期限短、流动性强、金额确定、价值变动风险小四项条件及可随时支付")
            normalized_details.append(key + ":" + ",".join(criteria))
        normalized[key] = status
    included = frozenset(key for key, value in normalized.items() if value == "include")
    excluded = frozenset(key for key, value in normalized.items() if value == "exclude")
    names = tuple((candidate.account_key, candidate.account_names) for candidate in proposal.candidates)
    source = "|".join(
        [*(f"{key}:{normalized[key]}" for key in sorted(expected)), *sorted(normalized_details)]
    )
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
    if entry.flow_amount_cent:
        return "借贷列+流量金额"
    return "借贷差额"


def _signed_flow(entry: NormalizedEntry) -> int:
    if entry.flow_amount_cent:
        account_delta = entry.debit_cent - entry.credit_cent
        side_delta = (
            abs(entry.flow_amount_cent)
            if account_delta > 0
            else -abs(entry.flow_amount_cent)
            if account_delta < 0
            else entry.flow_amount_cent
        )
    else:
        side_delta = entry.debit_cent - entry.credit_cent
    return -side_delta if entry.retained_side == "counterpart" else side_delta


def compute_rough_reconciliation(
    entries: Sequence[NormalizedEntry],
    profiles: Mapping[str, object],
    opening_cent: int,
    closing_cent: int,
    fx_cent: int,
) -> RoughReconciliation:
    """单边现流明细在分类前与期末-期初-汇率影响对比；序时账形态不适用。"""
    applicable_ids = tuple(
        sorted(
            file_id
            for file_id, profile in profiles.items()
            if profile.has_flow_amount
            and profile.retained_side_values <= frozenset({"counterpart"})
        )
    )
    if not applicable_ids:
        return RoughReconciliation(False, "不适用", None, None, None, ())
    detail_sum = sum(
        _signed_flow(entry)
        for entry in entries
        if entry.source.file_id in applicable_ids
    )
    expected = closing_cent - opening_cent - fx_cent
    difference = detail_sum - expected
    return RoughReconciliation(
        True,
        "相符" if difference == 0 else "存在差异",
        detail_sum,
        expected,
        difference,
        applicable_ids,
    )


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


def _voucher_non_cash_marks(entries: Sequence[NormalizedEntry], has_cash_leg: bool = False) -> tuple[str, ...]:
    """按凭证判定是否非现金事项，返回新增 anomaly 标记（保守：拿不准不标）。"""
    marks: list[str] = []
    if entries and all(
        any(term in entry.summary for term in NONCASH_SUMMARY_TERMS) for entry in entries
    ):
        marks.append("non_cash" if not has_cash_leg else "accrual_with_cash_leg")
    return tuple(marks)


def _connected_summary(entries: Sequence[NormalizedEntry]) -> tuple[str, tuple[str, ...]]:
    summaries = tuple(dict.fromkeys(entry.summary for entry in entries if entry.summary))
    if len(summaries) == 1:
        return summaries[0], ()
    if not summaries:
        return "", ("summary_empty",)
    return "", ("summary_allocation_ambiguous",)


def _annotate_voucher_components(
    entries: Sequence[NormalizedEntry],
    components: Sequence[CashflowComponent],
    *,
    suppress_non_cash: bool = False,
    has_cash_leg: bool = False,
) -> list[CashflowComponent]:
    """为同一凭证的组件统一追加 non_cash / netting_suspect 标记。

    netting_suspect：同凭证同时存在大额流入与流出，却全部挂在同一方向标签时，
    标记送 AI 复核，不自动改分类。
    """
    extra = list(_voucher_non_cash_marks(entries, has_cash_leg))
    if suppress_non_cash:
        extra = [marker for marker in extra if marker != "non_cash"]
    directions = {1 if component.cash_delta_cent > 0 else -1 for component in components}
    if 1 in directions and -1 in directions:
        red_ids = {entry.entry_id for entry in entries if entry.flow_amount_cent < 0}
        red_explains = bool(red_ids) and all(
            (
                _keyword_direction(component.original_item_text) is not None
                and (
                    component.cash_delta_cent > 0
                    if _keyword_direction(component.original_item_text) == 1
                    else component.cash_delta_cent < 0
                )
            )
            or any(key in red_ids for key in component.source_keys)
            for component in components
        )
        label_dirs = {
            _keyword_direction(component.original_item_text)
            for component in components
            if component.original_item_text
        }
        if not red_explains and label_dirs and (label_dirs <= {1} or label_dirs <= {-1}):
            extra.append("netting_suspect")
    return [
        replace(component, anomalies=tuple(dict.fromkeys((*component.anomalies, *extra))))
        for component in components
    ]


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
        tuple(
            dict.fromkeys(
                (*entry.input_issues, *(() if usable_counterpart else ("one_sided_source",)))
            )
        ),
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
            if _account_identity(outgoing) == _account_identity(incoming):
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


def _unique_minimum_amount_rows(
    rows: Sequence[NormalizedEntry], target: int
) -> tuple[NormalizedEntry, ...]:
    combinations = _minimum_amount_row_combinations(rows, target)
    return combinations[0] if len(combinations) == 1 else ()


def _minimum_amount_row_combinations(
    rows: Sequence[NormalizedEntry], target: int
) -> tuple[tuple[NormalizedEntry, ...], ...]:
    if len(rows) > MAX_AMOUNT_COMBINATION_ROWS:
        return ()
    states: dict[int, tuple[int, tuple[tuple[int, ...], ...]]] = {0: (0, ((),))}
    for index, row in enumerate(rows):
        amount = _signed_flow(row)
        if not amount:
            continue
        updated = dict(states)
        for total, (count, index_sets) in tuple(states.items()):
            new_total = total + amount
            candidates = tuple((*indices, index) for indices in index_sets)
            existing = updated.get(new_total)
            if existing is None or count + 1 < existing[0]:
                updated[new_total] = (count + 1, candidates[:20])
            elif count + 1 == existing[0]:
                merged = tuple(
                    dict.fromkeys((*existing[1], *candidates))
                )
                updated[new_total] = (existing[0], merged[:20])
        if len(updated) > MAX_AMOUNT_COMBINATION_STATES:
            return ()
        states = updated
    matched = states.get(target)
    if matched is None:
        return ()
    return tuple(
        tuple(rows[index] for index in indices)
        for indices in matched[1]
        if indices
    )


def _voucher_components(
    voucher_key: str,
    entries: list[NormalizedEntry],
    cash_entries: list[NormalizedEntry],
    selected_entry_ids: tuple[str, ...] = (),
) -> tuple[list[CashflowComponent], list[InternalTransferLeg]]:
    noncash = [entry for entry in entries if entry not in cash_entries]
    imbalance = sum(entry.debit_cent - entry.credit_cent for entry in entries)
    cash_delta = sum(
        entry.debit_cent - entry.credit_cent for entry in cash_entries
    )
    base_anomalies = tuple(
        dict.fromkeys(
            (*(("voucher_unbalanced",) if imbalance else ()),
             *(issue for entry in entries for issue in entry.input_issues))
        )
    )
    counterpart_accounts = tuple(dict.fromkeys(entry.account_name for entry in noncash if entry.account_name))
    labeled = [entry for entry in noncash if entry.original_flow_item]
    positions = {entry.entry_id: position for position, entry in enumerate(entries)}

    if len(cash_entries) > 1 and all(entry.summary for entry in cash_entries):
        matched_groups = tuple(
            (
                cash_entry,
                tuple(
                    entry
                    for entry in noncash
                    if entry.summary == cash_entry.summary
                ),
            )
            for cash_entry in cash_entries
        )
        matched_ids = {
            entry.entry_id
            for _, matched in matched_groups
            for entry in matched
        }
        if (
            matched_ids == {entry.entry_id for entry in noncash}
            and all(matched for _, matched in matched_groups)
            and all(
                sum(_signed_flow(entry) for entry in matched)
                == cash_entry.debit_cent - cash_entry.credit_cent
                for cash_entry, matched in matched_groups
            )
        ):
            components = [
                _component(
                    voucher_key,
                    sequence,
                    cash_entry.debit_cent - cash_entry.credit_cent,
                    cash_entry.summary,
                    tuple(
                        dict.fromkeys(
                            entry.account_name
                            for entry in matched
                            if entry.account_name
                        )
                    ),
                    next(
                        (
                            entry.original_flow_item
                            for entry in matched
                            if entry.original_flow_item
                        ),
                        "",
                    ),
                    (cash_entry.entry_id, *(entry.entry_id for entry in matched)),
                    base_anomalies,
                    "strong",
                )
                for sequence, (cash_entry, matched) in enumerate(matched_groups, 1)
            ]
            _, excluded = _match_internal_transfers(
                voucher_key,
                cash_entries,
                tuple(item.cash_delta_cent for item in components),
            )
            return components, excluded

    def belongs_to_excluded_cash(entry: NormalizedEntry) -> bool:
        if _looks_like_cash(entry.account_name):
            return False
        position = positions[entry.entry_id]
        neighbors = entries[max(0, position - 1):position] + entries[position + 1:position + 2]
        return any(
            neighbor not in cash_entries
            and _looks_like_cash(neighbor.account_name)
            and neighbor.summary == entry.summary
            and _signed_flow(neighbor) == _signed_flow(entry)
            for neighbor in neighbors
        )

    labeled = [entry for entry in labeled if not belongs_to_excluded_cash(entry)]
    labeled_flow_rows = [entry for entry in labeled if entry.flow_amount_cent]
    cash_directions = {
        1 if _signed_flow(entry) > 0 else -1
        for entry in cash_entries
        if _signed_flow(entry)
    }
    if len(cash_directions) > 1:
        supported_ids: set[str] = set()
        for direction in cash_directions:
            cash_direction_total = sum(
                _signed_flow(entry)
                for entry in cash_entries
                if _signed_flow(entry) * direction > 0
            )
            same_direction_labels = tuple(
                entry
                for entry in labeled_flow_rows
                if _signed_flow(entry) * direction > 0
            )
            supported_ids.update(
                entry.entry_id
                for entry in _unique_minimum_amount_rows(
                    same_direction_labels, cash_direction_total
                )
            )
        labeled_flow_rows = [
            entry for entry in labeled_flow_rows if entry.entry_id in supported_ids
        ]
    if labeled_flow_rows:
        components = [
            _component(
                voucher_key,
                sequence,
                _signed_flow(entry),
                entry.summary,
                (entry.account_name,) if entry.account_name else (),
                entry.original_flow_item,
                tuple(item.entry_id for item in cash_entries) + (entry.entry_id,),
                tuple(
                    dict.fromkeys(
                        (
                            *(("voucher_unbalanced",) if imbalance else ()),
                            *entry.input_issues,
                            *(("summary_empty",) if not entry.summary else ()),
                        )
                    )
                ),
                "strong",
            )
            for sequence, entry in enumerate(labeled_flow_rows, 1)
        ]
        allocated = sum(item.cash_delta_cent for item in components)
        residual = cash_delta - allocated
        if residual:
            residual_entries = [entry for entry in noncash if entry not in labeled_flow_rows]
            residual_summary, residual_anomalies = _connected_summary(residual_entries)
            components.append(
                _component(
                    voucher_key,
                    len(components) + 1,
                    residual,
                    residual_summary,
                    tuple(
                        dict.fromkeys(
                            entry.account_name for entry in residual_entries if entry.account_name
                        )
                    ),
                    "",
                    tuple(entry.entry_id for entry in cash_entries),
                    tuple(
                        dict.fromkeys(
                            (*base_anomalies, *residual_anomalies, "unallocated_cash")
                        )
                    ),
                    "weak",
                )
            )
        remaining, excluded = _match_internal_transfers(
            voucher_key, cash_entries, tuple(item.cash_delta_cent for item in components)
        )
        if sum(remaining.values()):
            components[-1] = replace(
                components[-1],
                anomalies=tuple(
                    dict.fromkeys((*components[-1].anomalies, "cash_allocation_mismatch"))
                ),
                evidence_strength="weak",
            )
        return components, excluded

    amount_combinations = _minimum_amount_row_combinations(labeled, cash_delta)
    if selected_entry_ids:
        candidate_ids = {
            tuple(entry.entry_id for entry in combination)
            for combination in amount_combinations
        }
        if selected_entry_ids not in candidate_ids:
            raise ValueError(f"业务组成确认不属于既有候选组合：{voucher_key}")
        selected = set(selected_entry_ids)
        amount_matched_rows = tuple(
            entry for entry in labeled if entry.entry_id in selected
        )
    else:
        amount_matched_rows = (
            amount_combinations[0] if len(amount_combinations) == 1 else ()
        )
    if amount_matched_rows:
        components = [
            _component(
                voucher_key,
                sequence,
                _signed_flow(entry),
                entry.summary,
                (entry.account_name,) if entry.account_name else (),
                entry.original_flow_item,
                tuple(item.entry_id for item in cash_entries) + (entry.entry_id,),
                tuple(
                    dict.fromkeys(
                        (
                            *(('voucher_unbalanced',) if imbalance else ()),
                            *entry.input_issues,
                            *(('summary_empty',) if not entry.summary else ()),
                        )
                    )
                ),
                "strong",
            )
            for sequence, entry in enumerate(amount_matched_rows, 1)
        ]
        remaining, excluded = _match_internal_transfers(
            voucher_key, cash_entries, tuple(item.cash_delta_cent for item in components)
        )
        if sum(remaining.values()):
            components[-1] = replace(
                components[-1],
                anomalies=tuple(
                    dict.fromkeys((*components[-1].anomalies, "cash_allocation_mismatch"))
                ),
                evidence_strength="weak",
            )
        return components, excluded

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
        summary, summary_anomalies = _connected_summary(item_entries or noncash)
        return [
            _component(
                voucher_key,
                1,
                cash_delta,
                summary,
                counterpart_accounts,
                item,
                tuple(entry.entry_id for entry in cash_entries) + tuple(
                    entry.entry_id for entry in item_entries
                ),
                tuple(dict.fromkeys((*base_anomalies, *summary_anomalies))),
                "strong" if noncash else "weak",
            )
        ], excluded

    components: list[CashflowComponent] = []
    for sequence, item_entries in enumerate(by_item.values(), 1):
        item = item_entries[0].original_flow_item
        amount = sum(_signed_flow(entry) for entry in item_entries)
        item_accounts = tuple(dict.fromkeys(entry.account_name for entry in item_entries if entry.account_name))
        summary, summary_anomalies = _connected_summary(item_entries)
        components.append(
            _component(
                voucher_key,
                sequence,
                amount,
                summary,
                item_accounts,
                item,
                tuple(entry.entry_id for entry in cash_entries)
                + tuple(entry.entry_id for entry in item_entries),
                tuple(dict.fromkeys((*base_anomalies, *summary_anomalies))),
                "strong",
            )
        )
    allocated = sum(item.cash_delta_cent for item in components)
    residual = cash_delta - allocated
    if residual:
        residual_summary, residual_anomalies = _connected_summary(noncash)
        components.append(
            _component(
                voucher_key,
                len(components) + 1,
                residual,
                residual_summary,
                counterpart_accounts,
                "",
                tuple(entry.entry_id for entry in cash_entries),
                tuple(
                    dict.fromkeys(
                        (*base_anomalies, *residual_anomalies, "unallocated_cash")
                    )
                ),
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


def _build_source_allocations(
    entries: Sequence[NormalizedEntry],
    components: Sequence[CashflowComponent],
    scope: CashScope,
) -> tuple[ComponentSourceAllocation, ...]:
    entries_by_voucher: dict[str, list[NormalizedEntry]] = defaultdict(list)
    entry_by_id = {entry.entry_id: entry for entry in entries}
    for entry in entries:
        entries_by_voucher[entry.voucher_key].append(entry)

    allocations: list[ComponentSourceAllocation] = []
    for voucher_key, voucher_components in _group_components(components).items():
        cash_entries = [
            entry
            for entry in entries_by_voucher.get(voucher_key, ())
            if entry.account_name
            and account_key_for_entry(entry) in scope.included_keys
            and not (
                entry.counterpart_name
                and account_key_for_entry(entry, counterpart=True) in scope.included_keys
            )
        ]
        remaining = {entry.entry_id: _signed_flow(entry) for entry in cash_entries}
        for component in voucher_components:
            amount_left = component.cash_delta_cent
            for entry in cash_entries:
                available = remaining[entry.entry_id]
                if not amount_left or available * amount_left <= 0:
                    continue
                used = min(abs(available), abs(amount_left))
                allocated = used if amount_left > 0 else -used
                allocations.append(
                    ComponentSourceAllocation(
                        component.component_id,
                        entry.entry_id,
                        allocated,
                    )
                )
                remaining[entry.entry_id] -= allocated
                amount_left -= allocated
            if amount_left and not cash_entries:
                direct = next(
                    (
                        entry_by_id[key]
                        for key in component.source_keys
                        if key in entry_by_id
                        and _signed_flow(entry_by_id[key]) * amount_left > 0
                    ),
                    None,
                )
                if direct is not None:
                    allocations.append(
                        ComponentSourceAllocation(
                            component.component_id,
                            direct.entry_id,
                            amount_left,
                        )
                    )
    return tuple(allocations)


def _group_components(
    components: Sequence[CashflowComponent],
) -> dict[str, list[CashflowComponent]]:
    grouped: dict[str, list[CashflowComponent]] = defaultdict(list)
    for component in components:
        grouped[component.voucher_key].append(component)
    return grouped


def build_cashflow_components(
    entries: Sequence[NormalizedEntry],
    scope: CashScope,
    single_sided_file_ids: frozenset[str] = frozenset(),
    structure_selections: Mapping[str, tuple[str, ...]] | None = None,
    structure_selection_basis: Mapping[str, str] | None = None,
) -> ComponentBuildResult:
    """按凭证构建现金流业务组成，并保持现金腿金额不变量。"""
    grouped: dict[str, list[NormalizedEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.voucher_key].append(entry)

    components: list[CashflowComponent] = []
    excluded: list[InternalTransferLeg] = []
    structure_requests: list[ComponentStructureRequest] = []
    structure_selections = structure_selections or {}
    structure_selection_basis = structure_selection_basis or {}
    for voucher_key, voucher_entries in grouped.items():
        single_sided = all(
            entry.source.file_id in single_sided_file_ids for entry in voucher_entries
        )
        cash_entries = [
            entry
            for entry in voucher_entries
            if entry.account_name and account_key_for_entry(entry) in scope.included_keys
        ]
        if cash_entries:
            explicit_internal = [
                entry
                for entry in cash_entries
                if entry.counterpart_name
                and account_key_for_entry(entry, counterpart=True) in scope.included_keys
            ]
            if explicit_internal:
                excluded.extend(
                    InternalTransferLeg(voucher_key, entry.entry_id, abs(_signed_flow(entry)))
                    for entry in explicit_internal
                )
                cash_entries = [entry for entry in cash_entries if entry not in explicit_internal]
            if not cash_entries:
                continue
            built, internal = _voucher_components(
                voucher_key,
                voucher_entries,
                cash_entries,
                tuple(structure_selections.get(voucher_key, ())),
            )
            if (
                voucher_key not in structure_selections
                and any(
                    "summary_allocation_ambiguous" in item.anomalies
                    for item in built
                )
            ):
                noncash = [entry for entry in voucher_entries if entry not in cash_entries]
                labeled = [entry for entry in noncash if entry.original_flow_item]
                imbalance = sum(
                    entry.debit_cent - entry.credit_cent for entry in voucher_entries
                )
                cash_delta = (
                    sum(entry.debit_cent - entry.credit_cent for entry in cash_entries)
                    if len(voucher_entries) > 1 and imbalance == 0
                    else sum(_signed_flow(entry) for entry in cash_entries)
                )
                combinations = _minimum_amount_row_combinations(labeled, cash_delta)
                if len(combinations) > 1:
                    structure_requests.append(
                        ComponentStructureRequest(
                            voucher_key,
                            cash_delta,
                            tuple(
                                tuple(entry.entry_id for entry in combination)
                                for combination in combinations
                            ),
                            "存在多个同样精简且金额都能闭合的业务组成组合，不能自动选择",
                        )
                    )
            annotated = _annotate_voucher_components(
                voucher_entries, built, suppress_non_cash=single_sided, has_cash_leg=True
            )
            if (
                voucher_key in structure_selections
                and structure_selection_basis.get(voucher_key)
                != "independent_external"
            ):
                annotated = [
                    replace(
                        item,
                        anomalies=tuple(
                            dict.fromkeys(
                                (*item.anomalies, "path_depends_on_summary")
                            )
                        ),
                    )
                    for item in annotated
                ]
            components.extend(annotated)
            excluded.extend(internal)
            continue
        if any(entry.retained_side == "cash" for entry in voucher_entries):
            continue
        has_excluded_cash_account = any(
            entry.account_name
            and _looks_like_cash(entry.account_name)
            and account_key_for_entry(entry) in scope.excluded_keys
            for entry in voucher_entries
        )
        one_sided: list[CashflowComponent] = []
        for entry in voucher_entries:
            named_cash_counterpart = bool(
                entry.counterpart_name
                and account_key_for_entry(entry, counterpart=True) in scope.included_keys
            )
            if (
                entry.retained_side == "counterpart"
                and named_cash_counterpart
                and (entry.flow_amount_cent or entry.debit_cent or entry.credit_cent)
            ):
                one_sided.append(_build_one_sided(entry))
        components.extend(
            _annotate_voucher_components(
                voucher_entries, one_sided, suppress_non_cash=single_sided, has_cash_leg=False
            )
        )
    frozen_components = tuple(components)
    return ComponentBuildResult(
        frozen_components,
        tuple(excluded),
        0,
        _build_source_allocations(entries, frozen_components, scope),
        tuple(structure_requests),
    )
