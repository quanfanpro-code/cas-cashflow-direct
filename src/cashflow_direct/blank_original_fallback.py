from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    MaterialityAmounts,
)
from cashflow_direct.rule_registry import default_rule_registry


_FALLBACK_POLICY = default_rule_registry().evidence_policy["fallback"]
_ELIGIBLE_ORIGINAL_STATES = frozenset(_FALLBACK_POLICY["eligible_original_states"])
_KNOWN_DIRECTION_STATUSES = frozenset(_FALLBACK_POLICY["known_direction_statuses"])
_FINAL_UNRESOLVED_ACTIONS = frozenset(_FALLBACK_POLICY["eligible_unresolved_actions"])
_SOURCE_PRIORITY = tuple(_FALLBACK_POLICY["source_priority_on_tie"])
_DIRECTION_DEFAULT_ITEMS = dict(_FALLBACK_POLICY["direction_default_items"])


def _first_in_statement_order(
    candidates: tuple[str, ...],
    ordered_leaf_item_ids: tuple[str, ...],
) -> str:
    candidate_set = set(candidates)
    return next(
        (item_id for item_id in ordered_leaf_item_ids if item_id in candidate_set),
        "",
    )


def apply_blank_original_fallback(
    component: CashflowComponent,
    decision: ClassificationDecision,
    materiality: MaterialityAmounts,
    ordered_leaf_item_ids: tuple[str, ...],
    item_directions: Mapping[str, str],
) -> ClassificationDecision:
    """为低于实际执行重要性的空白原项目形成可重复的最终项目。"""
    if (
        decision.resolved
        or decision.excluded
        or decision.decision_action not in _FINAL_UNRESOLVED_ACTIONS
        or decision.original_item_state not in _ELIGIBLE_ORIGINAL_STATES
        or decision.direction_status not in _KNOWN_DIRECTION_STATUSES
        or component.cash_delta_cent == 0
        or abs(component.cash_delta_cent) >= materiality.performance_cent
    ):
        return decision

    actual_direction = "inflow" if component.cash_delta_cent > 0 else "outflow"
    source_scores = {
        "account_path": decision.account_path_quality,
        "summary": decision.summary_quality,
    }
    winning_source = max(
        _SOURCE_PRIORITY,
        key=lambda source: (source_scores[source], -_SOURCE_PRIORITY.index(source)),
    )
    source_candidates = (
        decision.account_path_candidate_item_ids
        if winning_source == "account_path"
        else decision.summary_candidate_item_ids
    ) or ()
    compatible_candidates = tuple(
        dict.fromkeys(
            item_id
            for item_id in source_candidates
            if item_directions.get(item_id) == actual_direction
        )
    )
    preferred_item_id = (
        decision.account_path_preferred_item_id
        if winning_source == "account_path"
        else decision.summary_preferred_item_id
    )

    if preferred_item_id in compatible_candidates:
        selected_item_id = preferred_item_id
        fallback_step = "source_preferred"
    elif decision.system_item_id in compatible_candidates:
        selected_item_id = decision.system_item_id
        fallback_step = "existing_system_preferred"
    else:
        selected_item_id = _first_in_statement_order(
            compatible_candidates,
            ordered_leaf_item_ids,
        )
        fallback_step = "statement_order"

    fallback_source = winning_source
    if not selected_item_id:
        selected_item_id = _DIRECTION_DEFAULT_ITEMS[actual_direction]
        fallback_source = "cash_direction"
        fallback_step = "direction_other_operating"

    from cashflow_direct.classification import load_rule_pack

    rules = load_rule_pack(Path(__file__).resolve().parents[2])
    item = rules.item_by_id[selected_item_id]
    return replace(
        decision,
        system_item_id=item.item_id,
        system_item_name=item.name,
        normal_direction=item.normal_direction,
        resolved=True,
        decision_source="system_low_amount_fallback",
        decision_action="automatic_fill",
        fallback_source=fallback_source,
        fallback_step=fallback_step,
        reason=(
            f"{decision.reason}；低于实际执行重要性，按较高分独立来源兜底；"
            "同分优先完整科目路径来源"
            + (
                "；无来源候选，按现金方向落入经营活动其他收付"
                if fallback_step == "direction_other_operating"
                else ""
            )
        ),
    )
