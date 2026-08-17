from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from cashflow_direct.classification import RulePack
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


@dataclass(frozen=True, slots=True)
class ConsistencyTask:
    task_id: str
    group_id: str
    component_ids: tuple[str, ...]
    current_assignments: tuple[tuple[str, str], ...]
    gross_cent: int
    net_cent: int
    tier: str
    context: str


@dataclass(frozen=True, slots=True)
class ConsistencyResult:
    task_id: str
    group_id: str
    assignments: tuple[tuple[str, str], ...]
    reason: str
    confidence: str


@dataclass(frozen=True, slots=True)
class ConsistencyValidation:
    valid_results: tuple[ConsistencyResult, ...]
    missing_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    invalid_ids: tuple[str, ...]
    status: str


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


def _tier(gross_cent: int, materiality: MaterialityAmounts) -> str:
    if gross_cent < materiality.trivial_cent:
        return "trace_only"
    if gross_cent < materiality.performance_cent:
        return "first_review"
    if gross_cent < materiality.overall_cent:
        return "adjudication_required"
    return "double_high_required"


def find_consistency_groups(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    materiality: MaterialityAmounts,
) -> tuple[ConsistencyGroup, ...]:
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
        grouped[(source_files, component.voucher_key, summary)].append(component)

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
                tier=_tier(gross_cent, materiality),
                context=str(context),
            )
        )
    return tuple(result)


def build_consistency_tasks(
    groups: Sequence[ConsistencyGroup],
) -> tuple[ConsistencyTask, ...]:
    return tuple(
        ConsistencyTask(
            task_id=stable_id("CAI", group.group_id, *group.component_ids),
            group_id=group.group_id,
            component_ids=group.component_ids,
            current_assignments=group.current_assignments,
            gross_cent=group.gross_cent,
            net_cent=group.net_cent,
            tier=group.tier,
            context=group.context,
        )
        for group in groups
        if group.tier != "trace_only"
    )


def build_consistency_adjudication_tasks(
    groups: Sequence[ConsistencyGroup],
    first_results: Sequence[ConsistencyResult],
) -> tuple[ConsistencyTask, ...]:
    first_by_group = {item.group_id: item for item in first_results}
    tasks: list[ConsistencyTask] = []
    for group in groups:
        if group.tier not in {"adjudication_required", "double_high_required"}:
            continue
        first = first_by_group.get(group.group_id)
        first_context = (
            "第一轮结果缺失"
            if first is None
            else (
                f"第一轮项目：{dict(first.assignments)}；"
                f"第一轮理由：{first.reason}；第一轮置信度：{first.confidence}"
            )
        )
        tasks.append(
            ConsistencyTask(
                task_id=stable_id("CADJ", group.group_id, *group.component_ids),
                group_id=group.group_id,
                component_ids=group.component_ids,
                current_assignments=group.current_assignments,
                gross_cent=group.gross_cent,
                net_cent=group.net_cent,
                tier=group.tier,
                context=f"{group.context}\n{first_context}",
            )
        )
    return tuple(tasks)


def validate_consistency_results(
    expected: Sequence[ConsistencyTask],
    payloads: Sequence[Mapping[str, object]],
    valid_item_ids: set[str],
) -> ConsistencyValidation:
    expected_by_id = {task.task_id: task for task in expected}
    counts = Counter(str(payload.get("task_id", "")) for payload in payloads)
    duplicate_ids = tuple(
        sorted(task_id for task_id, count in counts.items() if task_id and count > 1)
    )
    valid: list[ConsistencyResult] = []
    invalid: set[str] = set()
    for payload in payloads:
        task_id = str(payload.get("task_id", ""))
        task = expected_by_id.get(task_id)
        assignments = payload.get("assignments")
        reason = payload.get("reason")
        confidence = payload.get("confidence")
        group_id = str(payload.get("group_id", ""))
        assignment_map = assignments if isinstance(assignments, Mapping) else {}
        is_valid = (
            task is not None
            and counts[task_id] == 1
            and group_id == task.group_id
            and set(assignment_map) == set(task.component_ids)
            and all(
                isinstance(component_id, str)
                and isinstance(item_id, str)
                and item_id in valid_item_ids
                for component_id, item_id in assignment_map.items()
            )
            and isinstance(reason, str)
            and bool(reason.strip())
            and confidence in {"high", "medium", "low"}
        )
        if not is_valid:
            if task_id:
                invalid.add(task_id)
            continue
        valid.append(
            ConsistencyResult(
                task_id=task_id,
                group_id=group_id,
                assignments=tuple(
                    (component_id, str(assignment_map[component_id]))
                    for component_id in task.component_ids
                ),
                reason=reason.strip(),
                confidence=str(confidence),
            )
        )
    valid_ids = {item.task_id for item in valid}
    missing_ids = tuple(sorted(set(expected_by_id) - valid_ids))
    status = (
        "AI 已完成"
        if not missing_ids and not duplicate_ids and not invalid
        else "AI 未完成"
    )
    return ConsistencyValidation(
        tuple(valid),
        missing_ids,
        duplicate_ids,
        tuple(sorted(invalid)),
        status,
    )


def merge_consistency_results(
    expected: Sequence[ConsistencyTask],
    prior_results: Sequence[ConsistencyResult],
    payloads: Sequence[Mapping[str, object]],
    valid_item_ids: set[str],
) -> ConsistencyValidation:
    incoming = validate_consistency_results(expected, payloads, valid_item_ids)
    merged = {item.task_id: item for item in prior_results}
    for item in incoming.valid_results:
        prior = merged.get(item.task_id)
        if prior is not None and prior != item:
            raise ValueError(f"一致性任务 {item.task_id} 与已导入结果冲突")
        merged[item.task_id] = item
    ordered = tuple(
        merged[task.task_id] for task in expected if task.task_id in merged
    )
    missing_ids = tuple(
        task.task_id for task in expected if task.task_id not in merged
    )
    status = (
        "AI 已完成"
        if not missing_ids and not incoming.duplicate_ids and not incoming.invalid_ids
        else "AI 未完成"
    )
    return ConsistencyValidation(
        ordered,
        missing_ids,
        incoming.duplicate_ids,
        incoming.invalid_ids,
        status,
    )


def _candidate_items(
    group: ConsistencyGroup,
    first: ConsistencyResult | None,
    second: ConsistencyResult | None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    assignment_sets: dict[str, set[str]] = {
        component_id: {item_id}
        for component_id, item_id in group.current_assignments
    }
    for result in (first, second):
        if result is None:
            continue
        for component_id, item_id in result.assignments:
            if component_id in assignment_sets:
                assignment_sets[component_id].add(item_id)
    group_candidates = {
        item_id for item_ids in assignment_sets.values() for item_id in item_ids
    }
    return tuple(
        (component_id, tuple(sorted(item_ids | group_candidates)))
        for component_id, item_ids in assignment_sets.items()
    )


def _apply_result(
    decisions: Sequence[ClassificationDecision],
    group: ConsistencyGroup,
    result: ConsistencyResult,
    rules: RulePack,
    source: str,
) -> tuple[ClassificationDecision, ...]:
    assignments = dict(result.assignments)
    group_components = set(group.component_ids)
    updated: list[ClassificationDecision] = []
    for decision in decisions:
        if decision.component_id not in group_components:
            updated.append(decision)
            continue
        item_id = assignments[decision.component_id]
        item = rules.item_by_id[item_id]
        updated.append(
            replace(
                decision,
                system_item_id=item_id,
                system_item_name=item.name,
                normal_direction=item.normal_direction,
                matched_rule_id=(
                    "CONSISTENCY-ADJUDICATED"
                    if source == "consistency_adjudication"
                    else "CONSISTENCY-REVIEWED"
                ),
                reason=result.reason,
                evidence_level=result.confidence,
                decision_source=source,
                resolved=True,
            )
        )
    return tuple(updated)


def resolve_consistency_groups(
    groups: Sequence[ConsistencyGroup],
    decisions: Sequence[ClassificationDecision],
    first_results: Sequence[ConsistencyResult],
    second_results: Sequence[ConsistencyResult],
    rules: RulePack,
) -> ConsistencyResolution:
    first_by_group = {item.group_id: item for item in first_results}
    second_by_group = {item.group_id: item for item in second_results}
    current = tuple(decisions)
    unresolved: list[UnresolvedConsistencyGroup] = []
    statuses: list[tuple[str, str, str, str]] = []
    for group in groups:
        first = first_by_group.get(group.group_id)
        second = second_by_group.get(group.group_id)
        selected: ConsistencyResult | None = None
        source = ""
        status = ""
        reason = ""
        if group.tier == "trace_only":
            status = "低于明显微小错报临界值"
            reason = "保留逐条判断，仅记录同一业务组项目不一致"
        elif group.tier == "first_review":
            if first is not None and first.confidence in {"medium", "high"}:
                selected = first
                source = "consistency_review"
                status = "一致性复核已收口"
                reason = first.reason
            else:
                status = "低于实际执行重要性且复核证据不足"
                reason = "保留逐条判断，不升级人工"
        elif group.tier == "adjudication_required":
            if second is not None and second.confidence == "high":
                selected = second
                source = "consistency_adjudication"
                status = "一致性裁决已收口"
                reason = second.reason
            else:
                status = "一致性裁决未收口"
                reason = "达到实际执行重要性，但第二轮未形成高置信度结论"
        else:
            if (
                first is not None
                and second is not None
                and first.confidence == "high"
                and second.confidence == "high"
                and first.assignments == second.assignments
            ):
                selected = second
                source = "consistency_adjudication"
                status = "重大一致性复核已收口"
                reason = second.reason
            else:
                status = "重大一致性复核未收口"
                reason = "达到整体重要性，两轮必须均为高置信度且逐条项目完全一致"

        if selected is not None:
            current = _apply_result(current, group, selected, rules, source)
        elif group.tier in {"adjudication_required", "double_high_required"}:
            unresolved.append(
                UnresolvedConsistencyGroup(
                    group_id=group.group_id,
                    component_ids=group.component_ids,
                    gross_cent=group.gross_cent,
                    candidate_item_ids=_candidate_items(group, first, second),
                    reason=reason,
                )
            )
        statuses.append((group.group_id, status, reason, group.tier))
    return ConsistencyResolution(current, tuple(unresolved), tuple(statuses))
