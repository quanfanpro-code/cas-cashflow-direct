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
    company_notes: Sequence[Mapping[str, object]] = (),
) -> AITask:
    # 复核修复：只向 AI 上下文注入"采用"状态的公司特殊规则
    company_notes = [note for note in company_notes if note.get("状态", "采用") == "采用"]
    context = redact_text(
        f"摘要：{component.summary}；对方科目：{'、'.join(component.counterpart_accounts)}；"
        f"现金金额分：{component.cash_delta_cent}；异常：{'、'.join(component.anomalies)}"
    )
    if company_notes:
        relevant = [
            str(note.get("内容", ""))
            for note in company_notes
            if any(
                term
                and (
                    term in component.summary
                    or any(term in account for account in component.counterpart_accounts)
                )
                for term in note.get("涉及科目或词", ())
            )
        ]
        if not relevant:
            relevant = [str(note.get("内容", "")) for note in company_notes]
        context += "；公司特殊规则：" + "；".join(relevant)
    return AITask(
        task_id=stable_id("AI", component.component_id, decision.system_item_id),
        component_id=component.component_id,
        context=context,
        original_item=component.original_item_text,
        system_item_id=decision.system_item_id,
        rule_evidence=f"{decision.matched_rule_id}：{decision.reason}",
    )


def review_text_pattern(text: str) -> str:
    """复核分组用的文本模式：剔除日期、数字与标点，仅保留业务文字。"""
    without_dates = re.sub(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", "", text)
    without_numbers = re.sub(r"\d[\d,，.]*", "", without_dates)
    normalized = re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】_-]+", "", without_numbers)
    return normalized.lower() or "空白"


def select_ai_tasks(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    materiality: MaterialityAmounts,
    company_notes: Sequence[Mapping[str, object]] = (),
) -> tuple[AITask, ...]:
    """复核路由分层：冲突、异常、普通低证据分别走不同重要性门槛。

    - 业务规则冲突（`BUSINESS-RULE-CONFLICT`）：不分评分档位，达明显微小
      临界值即送，低于则进同类累计池。
    - 保留原标签的冲突（`label_kept`）：评分 40-69 达明显微小临界值即送；
      评分不足 40 须达实际执行重要性；其余进同类累计池。
    - 异常类：达明显微小临界值即送，否则进同类累计池。
    - 普通低证据（评分不足 70 或证据强度弱）：达实际执行重要性才送，
      低于门槛不送、也不进累计池，只在台账留痕。
    - 同类累计池：同方向、同原现流项目、同系统项目、同对方科目、同摘要
      模式的分为一组，组内合计毛额达实际执行重要性则整组逐笔送。
    达到财务报表整体重要性的一律不送 AI，留待大额强制人工复核。
    """
    decision_by_component = {item.component_id: item for item in decisions}
    direct: list[tuple[CashflowComponent, ClassificationDecision]] = []
    pooled: list[tuple[CashflowComponent, ClassificationDecision]] = []
    for component in components:
        decision = decision_by_component[component.component_id]
        if decision.excluded:
            continue
        amount = abs(component.cash_delta_cent)
        # 达到财务报表整体重要性的强约束：不送 AI，留待大额强制人工复核
        if amount >= materiality.overall_cent:
            continue
        score = decision.evidence_score
        if decision.matched_rule_id == "BUSINESS-RULE-CONFLICT":
            # 业务规则冲突不分评分档位：达明显微小临界值即送，低于则进累计池
            if amount >= materiality.trivial_cent:
                direct.append((component, decision))
            else:
                pooled.append((component, decision))
        elif decision.label_kept:
            # 保留原标签的冲突：40-69 分档达明显微小临界值即送；不足 40 分须达实际执行重要性
            if 40 <= score < 70 and amount >= materiality.trivial_cent:
                direct.append((component, decision))
            elif score < 40 and amount >= materiality.performance_cent:
                direct.append((component, decision))
            else:
                pooled.append((component, decision))
        elif component.anomalies:
            if amount >= materiality.trivial_cent:
                direct.append((component, decision))
            else:
                pooled.append((component, decision))
        elif score < 70 or component.evidence_strength == "weak":
            if amount >= materiality.performance_cent:
                direct.append((component, decision))
    tasks = [
        build_ai_task(component, decision, company_notes)
        for component, decision in direct
    ]
    groups: dict[tuple[object, ...], list[tuple[CashflowComponent, ClassificationDecision]]] = {}
    for component, decision in pooled:
        key = (
            "inflow" if component.cash_delta_cent > 0 else "outflow",
            component.original_item_text,
            decision.system_item_id,
            tuple(sorted(component.counterpart_accounts)),
            review_text_pattern(component.summary),
        )
        groups.setdefault(key, []).append((component, decision))
    for members in groups.values():
        total = sum(abs(component.cash_delta_cent) for component, _ in members)
        if total < materiality.performance_cent:
            continue
        note = f"；同类 {len(members)} 笔，累计金额 {total / 100:,.2f} 元"
        for component, decision in members:
            task = build_ai_task(component, decision, company_notes)
            tasks.append(replace(task, context=task.context + note))
    return tuple(tasks)


def chunk_ai_tasks(
    tasks: Sequence[AITask],
    size: int = 25,
) -> tuple[tuple[AITask, ...], ...]:
    if not 1 <= size <= 25:
        raise ValueError("AI 单批数量必须在 1 至 25 之间")
    frozen = tuple(tasks)
    return tuple(frozen[index : index + size] for index in range(0, len(frozen), size))


def validate_basis_text(text: str) -> str | None:
    """AI 依据门禁：理由必须含四类可追查依据之一，否则返回拒绝原因。

    四类依据：准则条款（含"准则"且含"第×条"或"第×项"）、应用指南（含"应用指南"且含章节号
    或不短于 4 字的引文）、知识库（含"知识库"且含行号/章节/引文）、公司特殊规则
    NOTE 编号。只在导入校验环节把关，防止空泛理由进入复核链。
    """
    stripped = (text or "").strip()
    if not stripped:
        return "理由为空"
    if re.search(r"NOTE-\d+", stripped):
        return None
    clause = re.search(r"第[0-9一二三四五六七八九十百]+[条项]", stripped)
    if "准则" in stripped and clause:
        return None
    quoted = re.search(r"[\"'“”‘’《》][^\"'“”‘’《》]{4,}[\"'“”‘’《》]", stripped)
    chapter = re.search(r"第[0-9一二三四五六七八九十百]+[章节]", stripped)
    if "应用指南" in stripped and (chapter or quoted):
        return None
    if "知识库" in stripped and (re.search(r"第?\d+行", stripped) or chapter or quoted):
        return None
    return "理由缺少可追查依据（须含准则条款、应用指南章节或引文、知识库位置或 NOTE-编号）"


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
            and validate_basis_text(reason) is None
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
    item_names: Mapping[str, str],
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
            system.evidence_score >= 70 and adjudicated.confidence != "high"
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
        # 复核修复：改判后名称用真实项目名（此前误写成编号），旧规则证据清零、不随改判继承
        resolved.append(
            replace(
                system,
                system_item_id=adjudicated.item_id,
                system_item_name=item_names[adjudicated.item_id],
                matched_rule_id="AI-ADJUDICATED",
                reason=adjudicated.reason,
                evidence_level=adjudicated.confidence,
                decision_source="ai_adjudication",
                resolved=True,
                evidence_score=0,
                evidence_sources=(),
            )
        )
    return tuple(resolved)
