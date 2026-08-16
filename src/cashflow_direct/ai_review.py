from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from cashflow_direct.models import (
    AITask,
    CashflowComponent,
    ClassificationDecision,
    MaterialityAmounts,
)
from cashflow_direct.money import stable_id


@dataclass(frozen=True, slots=True)
class AIResult:
    task_id: str
    component_id: str
    item_id: str
    reason: str
    confidence: str


@dataclass(frozen=True, slots=True)
class AIValidation:
    valid_results: tuple[AIResult, ...]
    missing_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    invalid_ids: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class AdjudicationTask:
    task_id: str
    component_id: str
    system_item_id: str
    ai_item_id: str
    context: str


def redact_text(text: str) -> str:
    """在 AI 请求生成前遮蔽身份证、长账号和手机号。"""
    masked = re.sub(r"(?<!\d)\d{6}(?:19|20)\d{2}\d{2}\d{2}\d{3}[\dXx](?!\d)", "[身份证已遮蔽]", text)
    masked = re.sub(r"(?<!\d)\d{12,19}(?!\d)", "[账号已遮蔽]", masked)
    return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已遮蔽]", masked)


def build_ai_task(
    component: CashflowComponent,
    decision: ClassificationDecision,
) -> AITask:
    context = redact_text(
        f"摘要：{component.summary}；对方科目：{'、'.join(component.counterpart_accounts)}；"
        f"现金金额分：{component.cash_delta_cent}；异常：{'、'.join(component.anomalies)}"
    )
    return AITask(
        task_id=stable_id("AI", component.component_id, decision.system_item_id),
        component_id=component.component_id,
        context=context,
        original_item=component.original_item_text,
        system_item_id=decision.system_item_id,
        rule_evidence=f"{decision.matched_rule_id}：{decision.reason}",
    )


def select_ai_tasks(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    materiality: MaterialityAmounts,
) -> tuple[AITask, ...]:
    decision_by_component = {item.component_id: item for item in decisions}
    selected: list[AITask] = []
    for component in components:
        decision = decision_by_component[component.component_id]
        if decision.excluded:
            continue
        amount = abs(component.cash_delta_cent)
        if amount < materiality.trivial_cent:
            continue
        weak = component.evidence_strength == "weak"
        anomaly = bool(component.anomalies)
        urgent_conflict = decision.matched_rule_id in {
            "LABEL-RULE-CONFLICT",
            "LABEL-BUSINESS-HIGH-CONFLICT",
            "BUSINESS-RULE-CONFLICT",
        }
        evidence_selected = (
            decision.evidence_level in {"low", "medium"}
            and amount >= materiality.performance_cent
        )
        weak_selected = weak and amount >= materiality.performance_cent
        if urgent_conflict or evidence_selected or weak_selected or anomaly or not decision.resolved:
            selected.append(build_ai_task(component, decision))
    return tuple(selected)


def chunk_ai_tasks(
    tasks: Sequence[AITask],
    size: int = 25,
) -> tuple[tuple[AITask, ...], ...]:
    if not 1 <= size <= 25:
        raise ValueError("AI 单批数量必须在 1 至 25 之间")
    frozen = tuple(tasks)
    return tuple(frozen[index : index + size] for index in range(0, len(frozen), size))


def validate_ai_results(
    expected: Sequence[AITask],
    payloads: Sequence[Mapping[str, object]],
    valid_item_ids: set[str],
) -> AIValidation:
    expected_by_id = {task.task_id: task for task in expected}
    counts = Counter(str(payload.get("task_id", "")) for payload in payloads)
    duplicate_ids = tuple(sorted(task_id for task_id, count in counts.items() if task_id and count > 1))
    valid: list[AIResult] = []
    invalid: set[str] = set()
    for payload in payloads:
        task_id = str(payload.get("task_id", ""))
        task = expected_by_id.get(task_id)
        item_id = payload.get("item_id")
        reason = payload.get("reason")
        confidence = payload.get("confidence")
        component_id = str(payload.get("component_id", ""))
        is_valid = (
            task is not None
            and counts[task_id] == 1
            and component_id == task.component_id
            and isinstance(item_id, str)
            and item_id in valid_item_ids
            and isinstance(reason, str)
            and bool(reason.strip())
            and confidence in {"high", "medium", "low"}
        )
        if not is_valid:
            if task_id:
                invalid.add(task_id)
            continue
        valid.append(AIResult(task_id, component_id, item_id, reason.strip(), str(confidence)))
    valid_ids = {item.task_id for item in valid}
    missing = tuple(sorted(set(expected_by_id) - valid_ids))
    status = "AI 已完成" if not missing and not duplicate_ids and not invalid else "AI 未完成"
    return AIValidation(tuple(valid), missing, duplicate_ids, tuple(sorted(invalid)), status)


def merge_ai_results(
    expected: Sequence[AITask],
    prior_results: Sequence[AIResult],
    payloads: Sequence[Mapping[str, object]],
    valid_item_ids: set[str],
) -> AIValidation:
    incoming = validate_ai_results(expected, payloads, valid_item_ids)
    merged = {item.task_id: item for item in prior_results}
    invalid = set(incoming.invalid_ids)
    for item in incoming.valid_results:
        prior = merged.get(item.task_id)
        if prior is not None and prior != item:
            raise ValueError(f"AI 任务 {item.task_id} 与已导入结果冲突")
        merged[item.task_id] = item
    ordered = tuple(merged[task.task_id] for task in expected if task.task_id in merged)
    missing = tuple(task.task_id for task in expected if task.task_id not in merged)
    status = (
        "AI 已完成"
        if not missing and not incoming.duplicate_ids and not invalid
        else "AI 未完成"
    )
    return AIValidation(
        ordered,
        missing,
        incoming.duplicate_ids,
        tuple(sorted(invalid)),
        status,
    )


def write_ai_tasks_jsonl(path: Path, tasks: Sequence[AITask]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="\n") as output:
        for task in tasks:
            output.write(json.dumps(asdict(task), ensure_ascii=False, separators=(",", ":")) + "\n")


def build_adjudication_tasks(
    system_decisions: Sequence[ClassificationDecision],
    ai_results: Sequence[AIResult],
    ai_tasks: Sequence[AITask] = (),
) -> tuple[AdjudicationTask, ...]:
    system_by_component = {item.component_id: item for item in system_decisions}
    context_by_component = {item.component_id: item for item in ai_tasks}
    tasks: list[AdjudicationTask] = []
    for result in ai_results:
        system = system_by_component[result.component_id]
        if result.item_id == system.system_item_id:
            continue
        source_task = context_by_component.get(result.component_id)
        tasks.append(
            AdjudicationTask(
                stable_id("ADJ", result.component_id, system.system_item_id, result.item_id),
                result.component_id,
                system.system_item_id,
                result.item_id,
                "；".join(
                    part
                    for part in (
                        source_task.context if source_task is not None else "",
                        f"原现流项目：{source_task.original_item}"
                        if source_task is not None
                        else "",
                        f"系统证据：{system.reason}",
                        f"AI 证据：{result.reason}",
                    )
                    if part
                ),
            )
        )
    return tuple(tasks)


def resolve_automatic_decisions(
    system_decisions: Sequence[ClassificationDecision],
    ai_results: Sequence[AIResult],
    adjudication_results: Sequence[AIResult],
) -> tuple[ClassificationDecision, ...]:
    ai_by_component = {item.component_id: item for item in ai_results}
    adjudicated_by_component = {item.component_id: item for item in adjudication_results}
    resolved: list[ClassificationDecision] = []
    for system in system_decisions:
        ai = ai_by_component.get(system.component_id)
        if ai is None:
            resolved.append(system)
            continue
        if ai.item_id == system.system_item_id:
            resolved.append(
                replace(system, resolved=True, decision_source="ai_agreement")
            )
            continue
        adjudicated = adjudicated_by_component.get(system.component_id)
        if adjudicated is None or not adjudicated.reason.strip():
            resolved.append(replace(system, resolved=False, decision_source="ai_conflict"))
            continue
        if adjudicated.item_id not in {system.system_item_id, ai.item_id}:
            resolved.append(
                replace(
                    system,
                    resolved=False,
                    decision_source="ai_conflict",
                    reason=f"{system.reason}；AI 裁决超出系统首选与首次 AI 候选，送重要性判断",
                )
            )
            continue
        if adjudicated.confidence == "low" or (
            system.evidence_level == "high" and adjudicated.confidence != "high"
        ):
            resolved.append(
                replace(
                    system,
                    resolved=False,
                    decision_source="ai_conflict",
                    reason=f"{system.reason}；AI 裁决证据不足，保留系统首选并送重要性判断",
                )
            )
            continue
        resolved.append(
            replace(
                system,
                system_item_id=adjudicated.item_id,
                system_item_name=adjudicated.item_id,
                matched_rule_id="AI-ADJUDICATED",
                reason=adjudicated.reason,
                evidence_level=adjudicated.confidence,
                decision_source="ai_adjudication",
                resolved=True,
            )
        )
    return tuple(resolved)
