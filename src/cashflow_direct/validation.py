from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

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
    decision_ids = [item.component_id for item in decisions if not item.excluded]
    if len(component_ids) != len(set(component_ids)):
        errors.append("现金流业务组成编号不唯一")
    if set(component_ids) != set(decision_ids):
        errors.append("存在未取得唯一分类的现金流业务组成")
    if any(not item.system_item_id and not item.excluded for item in decisions):
        errors.append("存在空白正表项目分类")
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
