from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.components import ComponentSourceAllocation
from cashflow_direct.decision_policy import DecisionAction
from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.statement import StatementResult
from cashflow_direct.workbook_output import WorkbookModel, validate_output_workbook


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_input_hashes(files: Sequence[Mapping[str, object]]) -> ValidationResult:
    errors = []
    for item in files:
        path = Path(str(item["path"]))
        if not path.is_file() or _sha256(path) != item["sha256"]:
            errors.append(f"输入文件已被修改：{path.name}")
    return ValidationResult(not errors, tuple(errors))


def validate_classification(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
) -> ValidationResult:
    errors: list[str] = []
    component_ids = [item.component_id for item in components]
    decision_ids = [item.component_id for item in decisions]
    if not component_ids:
        errors.append("未生成现金流业务组成")
    if len(component_ids) != len(set(component_ids)):
        errors.append("现金流业务组成编号不唯一")
    if set(component_ids) != set(decision_ids):
        errors.append("存在未取得唯一分类的现金流业务组成")
    automatic_actions = {
        DecisionAction.AUTOMATIC_KEEP.value,
        DecisionAction.AUTOMATIC_FILL.value,
        DecisionAction.AUTOMATIC_CHANGE.value,
    }
    pending_actions = {
        DecisionAction.AI_REVIEW.value,
        DecisionAction.DOUBLE_AI_REVIEW.value,
        DecisionAction.AI_DOUBLE_FOLLOWUP_REVIEW.value,
        DecisionAction.AI_THIRD_REVIEW.value,
        DecisionAction.LOW_AMOUNT_HUMAN_BATCH.value,
        DecisionAction.HUMAN_BATCH.value,
        DecisionAction.HUMAN_DECISION.value,
        DecisionAction.ISOLATE_INVALID_INPUT.value,
        DecisionAction.CONFIRM_CASH_SCOPE.value,
        DecisionAction.CONFIRM_REVERSAL_RULE.value,
    }
    for item in decisions:
        if item.excluded:
            continue
        if item.resolved and not item.system_item_id:
            errors.append(f"已决定业务缺少正表项目：{item.component_id}")
        if item.resolved and item.decision_action in pending_actions:
            errors.append(f"已决定业务仍标成待处理动作：{item.component_id}")
        if not item.resolved and not item.decision_action:
            errors.append(f"待处理业务没有后续动作：{item.component_id}")
        if not item.resolved and item.decision_action in automatic_actions:
            errors.append(f"自动动作没有形成决定：{item.component_id}")
        if item.source_conflict and item.evidence_score is not None:
            errors.append(f"来源冲突仍形成了可用总分：{item.component_id}")
    return ValidationResult(not errors, tuple(errors))


def validate_final_readiness(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    source_allocations: Sequence[ComponentSourceAllocation],
    *,
    ai_tasks_missing: int = 0,
    mapping_complete: bool = True,
    versions_consistent: bool = True,
) -> ValidationResult:
    errors = list(validate_classification(components, decisions).errors)
    if ai_tasks_missing:
        errors.append(f"仍有 {ai_tasks_missing} 项AI复核没有完成")
    if not mapping_complete:
        errors.append("输入字段或一级科目映射仍未确认")
    if not versions_consistent:
        errors.append("本次运行的评分、规则或动作表版本已经变化")

    illegal_markers = {"summary_empty", "account_path_empty", "account_path_invalid"}
    decision_by_id = {item.component_id: item for item in decisions}
    illegal_components = [
        item.component_id
        for item in components
        if illegal_markers.intersection(item.anomalies)
        and not (
            (decision := decision_by_id.get(item.component_id))
            and decision.resolved
            and decision.decision_source == "manual"
        )
    ]
    if illegal_components:
        errors.append(
            "存在非法输入，补充摘要或完整对方科目路径前不能最终完成："
            + "、".join(illegal_components)
        )

    unresolved = [
        item.component_id
        for item in decisions
        if not item.resolved and not item.excluded
    ]
    if unresolved:
        errors.append("仍待人工决定：" + "、".join(unresolved))

    allocated_by_component: dict[str, int] = defaultdict(int)
    seen_pairs: set[tuple[str, str]] = set()
    for allocation in source_allocations:
        pair = (allocation.component_id, allocation.entry_id)
        if pair in seen_pairs:
            errors.append(
                f"原始行被重复分配：{allocation.component_id}/{allocation.entry_id}"
            )
        seen_pairs.add(pair)
        allocated_by_component[allocation.component_id] += allocation.allocated_cent
    component_ids = {item.component_id for item in components}
    unknown_allocations = sorted(set(allocated_by_component) - component_ids)
    if unknown_allocations:
        errors.append("金额分配指向不存在的业务组成：" + "、".join(unknown_allocations))
    for component in components:
        allocated = allocated_by_component.get(component.component_id, 0)
        if allocated != component.cash_delta_cent:
            errors.append(
                f"金额分配不守恒：{component.component_id}，"
                f"业务金额{component.cash_delta_cent}分，分配金额{allocated}分"
            )
    return ValidationResult(not errors, tuple(errors))


def validate_statement(statement: StatementResult) -> ValidationResult:
    errors: list[str] = []
    values = statement.values
    for prefix in ("CFO", "CFI", "CFF"):
        if values[f"{prefix}-IN"] - values[f"{prefix}-OUT"] != values[f"{prefix}-NET"]:
            errors.append(f"{prefix} 小计与净额不勾稽")
    expected = values["CFO-NET"] + values["CFI-NET"] + values["CFF-NET"] + values["FX"]
    if expected != values["NET-CASH"]:
        errors.append("现金及现金等价物净增加额不勾稽")
    if values["CASH-OPENING"] + values["NET-CASH"] != values["CASH-CLOSING"]:
        errors.append("期初余额、净增加额与期末余额不勾稽")
    return ValidationResult(not errors, tuple(errors))


def validate_final_output(path: Path, model: WorkbookModel) -> ValidationResult:
    checked = validate_output_workbook(path, model)
    return ValidationResult(checked.valid, checked.errors)
