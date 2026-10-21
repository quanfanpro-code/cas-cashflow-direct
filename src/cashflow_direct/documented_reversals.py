"""把明确引用原凭证的等额冲销归回原现金流项目。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date

from cashflow_direct.components import entry_cash_delta_cent
from cashflow_direct.models import CashflowComponent, ClassificationDecision, NormalizedEntry


_REFERENCE = re.compile(
    r"^[\[【]冲销(?P<date>\d{4}[./-]\d{1,2}[./-]\d{1,2})\s*"
    r"(?P<word>[^\s\-\]】]+)-(?P<number>\d+)号凭证[\]】]"
    r"(?P<summary>.*)$"
)


def _cash_source(component: CashflowComponent, entries: dict[str, NormalizedEntry]) -> NormalizedEntry | None:
    cash_rows = [
        entries[key]
        for key in component.source_keys
        if key in entries and entries[key].retained_side == "cash"
    ]
    if len(cash_rows) != 1 or entry_cash_delta_cent(cash_rows[0]) != component.cash_delta_cent:
        return None
    return cash_rows[0]


def apply_documented_reversals(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    entries: Sequence[NormalizedEntry],
    *,
    overall_cent: int,
    policy: Mapping[str, object],
) -> tuple[tuple[ClassificationDecision, ...], tuple[dict[str, object], ...]]:
    """仅在原凭证、现金账户、原现金摘要及相反金额均唯一吻合时抵销。"""
    required = (
        "require_unique_reference",
        "require_same_cash_account",
        "require_same_cash_summary",
        "require_equal_opposite_amount",
        "overall_materiality_still_manual",
    )
    if not policy.get("enabled"):
        return tuple(decisions), ()
    if any(policy.get(key) is not True for key in required):
        raise ValueError("明确冲销规则缺少逐项核对条件")
    rule_id = str(policy.get("rule_code", ""))
    if not rule_id:
        raise ValueError("明确冲销规则缺少规则编号")
    entry_by_id = {entry.entry_id: entry for entry in entries}
    decision_by_id = {decision.component_id: decision for decision in decisions}
    originals: dict[tuple[str, str, str, str, str, str, int], list[CashflowComponent]] = {}
    for component in components:
        source = _cash_source(component, entry_by_id)
        if source is None or _REFERENCE.match(source.summary.strip()):
            continue
        key = (
            component.voucher_date,
            str(int(component.voucher_no)) if component.voucher_no.isdigit() else component.voucher_no,
            source.voucher_word,
            source.source.file_id,
            source.account_code or source.account_name,
            source.summary.strip(),
            component.cash_delta_cent,
        )
        originals.setdefault(key, []).append(component)

    used_original_ids: set[str] = set()
    links: list[dict[str, object]] = []
    for reversal in components:
        source = _cash_source(reversal, entry_by_id)
        if source is None or reversal.cash_delta_cent == 0:
            continue
        reference = _REFERENCE.match(source.summary.strip())
        if reference is None:
            continue
        try:
            reference_date = date.fromisoformat(reference.group("date").replace(".", "-").replace("/", "-")).isoformat()
        except ValueError:
            continue
        key = (
            reference_date,
            str(int(reference.group("number"))),
            reference.group("word"),
            source.source.file_id,
            source.account_code or source.account_name,
            reference.group("summary").strip(),
            -reversal.cash_delta_cent,
        )
        matches = originals.get(key, ())
        if len(matches) != 1 or matches[0].component_id in used_original_ids:
            continue
        original = matches[0]
        original_decision = decision_by_id[original.component_id]
        if (
            not original_decision.resolved
            or original_decision.excluded
            or not original_decision.system_item_id
            or abs(reversal.cash_delta_cent) >= overall_cent
        ):
            continue
        current = decision_by_id[reversal.component_id]
        if current.excluded:
            continue
        basis = (
            f"摘要明确引用{reference_date} {reference.group('word')}-{reference.group('number')}号凭证；"
            "原凭证与冲销现金账户、原现金摘要、金额均逐项吻合且方向相反；"
            f"沿用原凭证项目{original_decision.system_item_id}，两笔在该项目内相抵"
        )
        decision_by_id[reversal.component_id] = replace(
            current,
            system_item_id=original_decision.system_item_id,
            system_item_name=original_decision.system_item_name,
            normal_direction=original_decision.normal_direction,
            matched_rule_id=rule_id,
            reason=basis,
            decision_source="system_documented_reversal",
            resolved=True,
            evidence_score=0,
            evidence_sources=(),
            candidate_item_ids=(original_decision.system_item_id,),
            summary_candidate_item_ids=(),
            account_path_candidate_item_ids=(),
            summary_preferred_item_id="",
            account_path_preferred_item_id="",
            sources_independent=False,
            source_conflict=False,
            business_conflict=False,
            direction_status="incompatible",
            decision_action="automatic_fill" if not current.original_standard_item_id else "automatic_change",
            ai_review_policy="",
            candidate_status="available",
            decision_rule_id=rule_id,
        )
        used_original_ids.add(original.component_id)
        links.append({
            "original_component_id": original.component_id,
            "reversal_component_id": reversal.component_id,
            "item_id": original_decision.system_item_id,
            "amount_cent": reversal.cash_delta_cent,
            "original_cash_entry_id": _cash_source(original, entry_by_id).entry_id,
            "reversal_cash_entry_id": source.entry_id,
            "basis": basis,
        })
    return tuple(decision_by_id[decision.component_id] for decision in decisions), tuple(links)
