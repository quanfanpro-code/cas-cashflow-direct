from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from cashflow_direct.decision_policy import (
    DecisionAction,
    MaterialityLevel,
    OriginalItemState,
    materiality_level,
    route_decision,
)
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    MaterialityAmounts,
)
from cashflow_direct.money import stable_id


@dataclass(frozen=True, slots=True)
class ConsistencyGroup:
    group_id: str
    component_ids: tuple[str, ...]
    current_assignments: tuple[tuple[str, str], ...]
    gross_cent: int
    net_cent: int
    tier: str
    context: str
    materiality_level: str = ""


@dataclass(frozen=True, slots=True)
class UnresolvedConsistencyGroup:
    group_id: str
    component_ids: tuple[str, ...]
    gross_cent: int
    candidate_item_ids: tuple[tuple[str, tuple[str, ...]], ...]
    reason: str


@dataclass(frozen=True, slots=True)
class ConsistencyResolution:
    decisions: tuple[ClassificationDecision, ...]
    unresolved: tuple[UnresolvedConsistencyGroup, ...]
    statuses: tuple[tuple[str, str, str, str], ...]


def _normalized_summary(value: str) -> str:
    return re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】]+", "", value).lower()


def _normalized_account_path(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(_normalized_summary(value) for value in values if value.strip()))


def find_consistency_groups(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    materiality: MaterialityAmounts,
) -> tuple[ConsistencyGroup, ...]:
    """只检查同一原始来源；相同摘要但完整路径不同的业务绝不合并。"""
    decision_by_component = {item.component_id: item for item in decisions}
    grouped: dict[tuple[object, ...], list[CashflowComponent]] = defaultdict(list)
    for component in components:
        decision = decision_by_component.get(component.component_id)
        summary = _normalized_summary(component.summary)
        source_files = tuple(sorted(component.source_file_ids))
        if (
            decision is None
            or decision.excluded
            or not component.voucher_key
            or not summary
            or not source_files
        ):
            continue
        grouped[
            (
                source_files,
                component.voucher_key,
                summary,
                _normalized_account_path(component.counterpart_accounts),
            )
        ].append(component)

    result: list[ConsistencyGroup] = []
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        assignments = tuple(
            (item.component_id, decision_by_component[item.component_id].system_item_id)
            for item in items
        )
        if len({item_id for _, item_id in assignments}) < 2:
            continue
        gross_cent = sum(abs(item.cash_delta_cent) for item in items)
        net_cent = sum(item.cash_delta_cent for item in items)
        context = "\n".join(
            "；".join(
                (
                    f"组成 {item.component_id}",
                    f"日期：{item.voucher_date}",
                    f"凭证号：{item.voucher_no}",
                    f"摘要：{item.summary}",
                    f"对方科目：{'、'.join(item.counterpart_accounts)}",
                    f"现金金额分：{item.cash_delta_cent}",
                    f"原现流项目：{item.original_item_text}",
                    f"当前项目：{decision_by_component[item.component_id].system_item_name}",
                    f"当前理由：{decision_by_component[item.component_id].reason}",
                )
            )
            for item in items
        )
        result.append(
            ConsistencyGroup(
                group_id=stable_id(
                    "CGR", *key, *(item.component_id for item in items)
                ),
                component_ids=tuple(item.component_id for item in items),
                current_assignments=assignments,
                gross_cent=gross_cent,
                net_cent=net_cent,
                tier=materiality_level(gross_cent, materiality).value,
                context=context,
                materiality_level=materiality_level(gross_cent, materiality).value,
            )
        )
    return tuple(result)


def apply_consistency_forced_checks(
    groups: Sequence[ConsistencyGroup],
    decisions: Sequence[ClassificationDecision],
    item_names: Mapping[str, str] | None = None,
    item_directions: Mapping[str, str] | None = None,
) -> ConsistencyResolution:
    """同一组原始来源却出现不同项目时，只把冲突事实交给统一行动中心。"""
    current = {item.component_id: item for item in decisions}
    unresolved: list[UnresolvedConsistencyGroup] = []
    statuses: list[tuple[str, str, str, str]] = []
    for group in groups:
        candidates = tuple(
            sorted({item_id for _, item_id in group.current_assignments})
        )
        reason = (
            "同一凭证的本行摘要和完整对方科目路径相同，却形成不同候选，"
            "属于业务事实冲突；证据分数原样保留，按业务事实冲突强制检查处理"
        )
        pending_component_ids: list[str] = []
        for component_id in group.component_ids:
            decision = current[component_id]
            level = MaterialityLevel(decision.materiality_level)
            original_state = OriginalItemState(
                decision.original_item_state or OriginalItemState.BLANK.value
            )
            route = route_decision(
                score=decision.evidence_score,
                original_state=original_state,
                materiality=level,
                invalid_input=(
                    decision.decision_action
                    == DecisionAction.ISOLATE_INVALID_INPUT.value
                ),
                company_rule_conflict=decision.company_rule_conflict,
                vat_base_missing=decision.vat_base_missing,
                net_item_facts_missing=decision.net_item_facts_missing,
                individual_tax_fact_missing=decision.individual_tax_fact_missing,
                new_reversal_pattern=decision.new_reversal_pattern,
                source_conflict=decision.source_conflict,
                business_conflict=True,
                direction_status=decision.direction_status or "compatible",
            )
            action = route.action
            resolved = action in {
                DecisionAction.AUTOMATIC_KEEP,
                DecisionAction.AUTOMATIC_FILL,
                DecisionAction.AUTOMATIC_CHANGE,
            }
            if (
                action is DecisionAction.AUTOMATIC_KEEP
                and decision.original_standard_item_id
            ):
                result_item_id = decision.original_standard_item_id
                result_item_name = (
                    item_names.get(result_item_id, result_item_id)
                    if item_names is not None
                    else result_item_id
                )
                result_direction = (
                    item_directions.get(result_item_id, decision.normal_direction)
                    if item_directions is not None
                    else decision.normal_direction
                )
            else:
                result_item_id = decision.system_item_id
                result_item_name = decision.system_item_name
                result_direction = decision.normal_direction
            if not resolved:
                pending_component_ids.append(component_id)
            current[component_id] = replace(
                decision,
                reason=f"{decision.reason}；{reason}",
                system_item_id=result_item_id,
                system_item_name=result_item_name,
                normal_direction=result_direction,
                resolved=resolved,
                business_conflict=True,
                decision_action=action.value,
                decision_source=(
                    "system_automatic" if resolved else decision.decision_source
                ),
            )
        if pending_component_ids:
            unresolved.append(
                UnresolvedConsistencyGroup(
                    group_id=group.group_id,
                    component_ids=tuple(pending_component_ids),
                    gross_cent=group.gross_cent,
                    candidate_item_ids=tuple(
                        (component_id, candidates)
                        for component_id in pending_component_ids
                    ),
                    reason=reason,
                )
            )
        statuses.append(
            (
                group.group_id,
                "等待人工决定" if pending_component_ids else "已保持原项目",
                reason,
                group.tier,
            )
        )
    ordered = tuple(current[item.component_id] for item in decisions)
    return ConsistencyResolution(ordered, tuple(unresolved), tuple(statuses))
