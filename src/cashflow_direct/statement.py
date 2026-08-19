from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.classification import RulePack
from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.money import statement_amount_cent, yuan_to_cent
from cashflow_direct.semantic_mapping import ColumnMapping, MappingQuestion
from cashflow_direct.workbook_structure import open_workbook_robust


@dataclass(frozen=True, slots=True)
class StatementResult:
    values: dict[str, int]
    prior_values: dict[str, int | None]
    support_component_ids: dict[str, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ExistingCustomRow:
    name: str
    parent_item_id: str
    current_cent: int | None
    prior_cent: int | None
    source_cell: str


@dataclass(frozen=True, slots=True)
class ExistingStatementResult:
    values: dict[str, int | None]
    prior_values: dict[str, int | None]
    standardized_values: dict[str, int | None]
    custom_rows: tuple[ExistingCustomRow, ...]
    unit_multiplier: int
    sheet_name: str = ""


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    item_id: str
    existing_cent: int | None
    computed_cent: int
    manual_adjustment_cent: int
    final_cent: int
    difference_cent: int | None
    support_component_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StatementComparison:
    rows: tuple[ComparisonRow, ...]


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: str
    opening_cent: int | None
    closing_cent: int | None
    fx_cent: int | None
    net_cash_cent: int | None
    difference_cent: int | None


def _migrate_negative_net(
    values: dict[str, int],
    support: dict[str, list[str]],
    source_id: str,
    target_id: str,
) -> None:
    """净额类项目为负时按应用指南迁移到对应"其他"投资活动项目。

    处置固定资产、无形资产和其他长期资产收回的现金净额（CFI-03）为负 →
    移入"支付其他与投资活动有关的现金"（CFI-09）；
    处置子公司及其他营业单位收到的现金净额（CFI-04）为负 →
    移入"支付其他与投资活动有关的现金"（CFI-09）；
    取得子公司及其他营业单位支付的现金净额（CFI-08）为负 →
    移入"收到其他与投资活动有关的现金"（CFI-05）。
    迁移连同支撑组成编号一并转移，保持留痕可追溯。
    """
    if values[source_id] >= 0:
        return
    values[target_id] += -values[source_id]
    values[source_id] = 0
    support[target_id].extend(support[source_id])
    support[source_id] = []


def aggregate_statement(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    rules: RulePack,
    *,
    opening_cent: int | None = None,
    fx_cent: int | None = None,
    prior_values: dict[str, int | None] | None = None,
) -> StatementResult:
    item_by_id = rules.item_by_id
    values = {item.item_id: 0 for item in rules.statement_items}
    support: dict[str, list[str]] = {item.item_id: [] for item in rules.statement_items}
    component_by_id = {item.component_id: item for item in components}
    for decision in decisions:
        if decision.excluded:
            continue
        component = component_by_id[decision.component_id]
        item = item_by_id[decision.system_item_id]
        if not item.is_leaf:
            raise ValueError(f"业务组成不能直接分类到汇总行：{item.item_id}")
        values[item.item_id] += statement_amount_cent(
            component.cash_delta_cent, item.normal_direction
        )
        support[item.item_id].append(component.component_id)

    # 净额类项目为负时的列报迁移（须在汇总行公式计算之前执行）
    _migrate_negative_net(values, support, "CFI-03", "CFI-09")
    _migrate_negative_net(values, support, "CFI-04", "CFI-09")
    _migrate_negative_net(values, support, "CFI-08", "CFI-05")

    if fx_cent is not None:
        values["FX"] = fx_cent
    if opening_cent is not None:
        values["CASH-OPENING"] = opening_cent
    for item in sorted(rules.statement_items, key=lambda value: value.display_order):
        if item.formula_components:
            values[item.item_id] = sum(
                values[source_id] * multiplier
                for source_id, multiplier in item.formula_components
            )
            support[item.item_id] = list(
                dict.fromkeys(
                    component_id
                    for source_id, _ in item.formula_components
                    for component_id in support[source_id]
                )
            )
    return StatementResult(
        values,
        {
            item.item_id: (prior_values or {}).get(item.item_id)
            for item in rules.statement_items
        },
        {item_id: tuple(component_ids) for item_id, component_ids in support.items()},
    )


def _normalize_item_name(value: str) -> str:
    text = re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】]+", "", value)
    return text.replace("以及", "及").replace("和", "").replace("其中", "")


# 常见措辞变体：归一化后的写法 → 归一化后的标准名（A2 模糊匹配别名表）
_ITEM_ALIASES = {
    "收到的税收返还": "收到的税费返还",
    "税收返还": "税费返还",
    "支付的其他与经营活动有关的现金": "支付其他与经营活动有关的现金",
    "收到的其他与经营活动有关的现金": "收到其他与经营活动有关的现金",
    "收到的其他与投资活动有关的现金": "收到其他与投资活动有关的现金",
    "支付的其他与投资活动有关的现金": "支付其他与投资活动有关的现金",
    "收到的其他与筹资活动有关的现金": "收到其他与筹资活动有关的现金",
    "支付的其他与筹资活动有关的现金": "支付其他与筹资活动有关的现金",
}


def _edit_distance_at_most(a: str, b: str, limit: int = 2) -> bool:
    """判断 a、b 编辑距离是否 ≤ limit；长度差超限直接 False。"""
    if abs(len(a) - len(b)) > limit:
        return False
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            cost = 0 if char_a == char_b else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        if min(current) > limit:
            return False
        previous = current
    return previous[len(b)] <= limit


def _mapping_question(
    role: str,
    text: str,
    row_number: int,
    sheet_name: str = "",
) -> MappingQuestion:
    candidate = ColumnMapping(
        role,
        1,
        "A",
        (text,),
        f"A{row_number}",
        0,
    )
    return MappingQuestion(role, candidate, (), (text, "", ""), sheet_name)


def _amount_to_cent(value: object, unit_multiplier: int) -> int | None:
    if value in (None, ""):
        return None
    return yuan_to_cent(value) * unit_multiplier


def _parse_statement_rows(
    rows: list[tuple[object, ...]],
    sheet_name: str,
    rules: RulePack,
    reference_years: frozenset[int] = frozenset(),
) -> ExistingStatementResult | MappingQuestion | None:
    unit_text = "|".join(
        str(value)
        for row in rows[:20]
        for value in row
        if value not in (None, "")
    )
    unit_multiplier = 10_000 if "万元" in unit_text else 1
    header_row = None
    project_column = None
    header_texts: list[str] = []
    for row_index, row in enumerate(rows, 1):
        texts = [_normalize_item_name(str(value)) if value is not None else "" for value in row]
        project = next((index for index, text in enumerate(texts) if text == "项目"), None)
        if project is not None:
            header_row = row_index
            project_column = project
            header_texts = texts
            break
    if header_row is None or project_column is None:
        return None

    normalized_to_id = {
        _normalize_item_name(item.name): item.item_id for item in rules.statement_items
    }
    # 模糊匹配命中多个不同项目的歧义记录：(归一化后文字, 候选项目id)
    ambiguities: list[tuple[str, tuple[str, ...]]] = []

    def _match_item_id(normalized: str) -> str | None:
        """多级匹配标准项目名：精确 → 序数前缀 → 别名 → 包含 → 编辑距离；歧义记入但不武断选中。"""
        item_id = normalized_to_id.get(normalized)
        if item_id is not None:
            return item_id
        stripped = re.sub(r"^[一二三四五六七八九十]+", "", normalized)
        item_id = normalized_to_id.get(stripped)
        if item_id is not None:
            return item_id
        alias = _ITEM_ALIASES.get(normalized) or _ITEM_ALIASES.get(stripped)
        if alias is not None:
            item_id = normalized_to_id.get(alias)
            if item_id is not None:
                return item_id
        standards = [
            (item.item_id, _normalize_item_name(item.name))
            for item in rules.statement_items
        ]
        fuzzy: list[str] = []
        for std_id, std in standards:
            if not std:
                continue
            shorter, longer = (stripped, std) if len(stripped) <= len(std) else (std, stripped)
            if len(shorter) >= 6 and shorter in longer:
                fuzzy.append(std_id)
        if not fuzzy:
            for std_id, std in standards:
                if not std:
                    continue
                if len(max(stripped, std, key=len)) >= 6 and _edit_distance_at_most(stripped, std):
                    fuzzy.append(std_id)
        unique = tuple(dict.fromkeys(fuzzy))
        if len(unique) == 1:
            return unique[0]
        if len(unique) > 1:
            ambiguities.append((stripped, unique))
        return None

    item_rows = [
        row for row in rows[header_row:]
        if project_column < len(row) and row[project_column] not in (None, "")
    ]
    hits = sum(
        1 for row in item_rows
        if _match_item_id(_normalize_item_name(str(row[project_column]))) is not None
    )
    if not item_rows or hits == 0:
        return None  # 项目列零命中，不是现流正表
    if hits / len(item_rows) < 0.5:
        return _mapping_question(
            "statement_header",
            f"项目列与标准现流项目匹配率过低（{hits}/{len(item_rows)}），未认定为现流正表",
            header_row,
            sheet_name,
        )

    prior_column = next(
        (index for index, text in enumerate(header_texts) if "上期" in text or "上年" in text),
        None,
    )

    def _numeric_share(column: int) -> float:
        values = [
            row[column] for row in item_rows
            if column < len(row) and row[column] not in (None, "")
        ]
        if not values:
            return 0.0
        numeric = sum(
            1 for value in values
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        )
        return numeric / len(values)

    def _is_sequence_column(column: int) -> bool:
        values = [
            row[column] for row in item_rows
            if column < len(row) and isinstance(row[column], (int, float)) and not isinstance(row[column], bool)
        ]
        return bool(values) and [int(value) for value in values] == list(range(1, len(values) + 1))

    candidates = [
        index
        for index in range(len(header_texts))
        if index != project_column
        and index != prior_column
        and "行次" not in header_texts[index]
        and "序号" not in header_texts[index]
        and _numeric_share(index) >= 0.6
        and not _is_sequence_column(index)
    ]
    if not candidates:
        return None
    best_share = max(_numeric_share(index) for index in candidates)
    tied = [index for index in candidates if _numeric_share(index) == best_share]
    if len(tied) > 1:
        preferred = [
            index for index in tied
            if any(word in header_texts[index] for word in ("本期", "本年", "金额"))
        ]
        if preferred:
            tied = preferred
    # 多时间列：并列时结合明细日期区间推断本期年份，命中含该年份表头的列（A3）
    if len(tied) > 1 and reference_years:
        year = max(reference_years)
        year_hits = [
            index for index in tied if str(year) in str(header_texts[index])
        ]
        if year_hits:
            tied = year_hits
    if len(tied) > 1:
        return _mapping_question("statement_header", "本期金额列存在并列候选，无法确定", header_row, sheet_name)
    current_column = tied[0]
    values: dict[str, int | None] = {}
    prior_values: dict[str, int | None] = {}
    custom_rows: list[ExistingCustomRow] = []
    last_standard_id = ""
    for row_number, row in enumerate(rows[header_row:], header_row + 1):
        if project_column >= len(row):
            continue
        raw_name = row[project_column]
        if raw_name in (None, ""):
            continue
        name = str(raw_name).strip()
        normalized = _normalize_item_name(name)
        stripped = re.sub(r"^[一二三四五六七八九十]+", "", normalized)
        item_id = _match_item_id(normalized)
        if item_id is None:
            ambiguous = next(
                (ids for text, ids in ambiguities if text == stripped), None
            )
            if ambiguous is not None:
                names = " / ".join(rules.item_by_id[item_id].name for item_id in ambiguous)
                return _mapping_question(
                    "statement_item",
                    f"该行可匹配多个标准项目：{names}，请确认",
                    row_number,
                    sheet_name,
                )
        current_value = row[current_column] if current_column < len(row) else None
        prior_value = row[prior_column] if prior_column is not None and prior_column < len(row) else None
        # 占位符（如"——"）视为无金额，节标题行才能正确跳过
        if isinstance(current_value, str) and current_value.strip() in {"——", "—", "–", "-", "―"}:
            current_value = None
        if isinstance(prior_value, str) and prior_value.strip() in {"——", "—", "–", "-", "―"}:
            prior_value = None
        # 纯空白字符串金额视为无金额（注释行等）
        if isinstance(current_value, str) and not current_value.strip():
            current_value = None
        if isinstance(prior_value, str) and not prior_value.strip():
            prior_value = None
        # 金融企业专用行（△ 前缀）金额全为零或无金额时不参与一般企业正表核对，直接跳过
        if name.startswith("△") and not current_value and not prior_value:
            continue
        if item_id is not None:
            values[item_id] = _amount_to_cent(current_value, unit_multiplier)
            prior_values[item_id] = _amount_to_cent(prior_value, unit_multiplier)
            last_standard_id = item_id
            continue
        if current_value in (None, ""):
            continue  # 节标题等无金额行跳过
        last_item = rules.item_by_id.get(last_standard_id)
        if last_standard_id and (
            name.startswith("其中")
            or (
                last_item is not None
                and last_item.is_leaf
                and not any(term in name for term in ("合计", "总额", "净额", "余额", "影响"))
            )
        ):
            custom_rows.append(
                ExistingCustomRow(
                    name,
                    last_standard_id,
                    _amount_to_cent(current_value, unit_multiplier),
                    _amount_to_cent(prior_value, unit_multiplier),
                    f"A{row_number}",
                )
            )
            continue
        return _mapping_question("statement_item", name, row_number, sheet_name)

    if len(values) != len(rules.statement_items):
        missing = next(
            item.item_id for item in rules.statement_items if item.item_id not in values
        )
        return _mapping_question(
            "statement_item",
            f"缺少标准项目 {missing}",
            header_row,
            sheet_name,
        )
    return ExistingStatementResult(
        values,
        prior_values,
        dict(values),
        tuple(custom_rows),
        unit_multiplier,
        sheet_name,
    )


def detect_statement_sheets(
    path: Path,
    rules: RulePack,
    reference_years: frozenset[int] = frozenset(),
) -> dict[str, ExistingStatementResult | MappingQuestion | None]:
    """逐工作表识别客户现有正表。键为工作表名，None 表示该表不是正表。

    提供按工作表粒度的识别能力，供"明细与正表同文件"双角色读取使用，
    避免一个文件只能被读成明细或正表中的单一角色。
    """
    workbook = open_workbook_robust(path)
    try:
        return {
            worksheet.title: _parse_statement_rows(
                list(worksheet.iter_rows(values_only=True)),
                worksheet.title,
                rules,
                reference_years,
            )
            for worksheet in workbook.worksheets
        }
    finally:
        workbook.close()


def parse_existing_statement(
    path: Path,
    rules: RulePack,
    reference_years: frozenset[int] = frozenset(),
) -> ExistingStatementResult | MappingQuestion:
    candidates = tuple(detect_statement_sheets(path, rules, reference_years).values())
    valid = tuple(item for item in candidates if isinstance(item, ExistingStatementResult))
    if len(valid) > 1:
        names = "、".join(item.sheet_name for item in valid)
        return _mapping_question(
            "statement_sheet",
            f"识别到多个现金流量表工作表：{names}",
            1,
        )
    if valid:
        return valid[0]
    question = next((item for item in candidates if isinstance(item, MappingQuestion)), None)
    return question or _mapping_question("statement_header", "未找到项目列或可用金额列", 1)


def compare_statement(
    existing: ExistingStatementResult,
    computed: StatementResult,
) -> StatementComparison:
    rows: list[ComparisonRow] = []
    for item_id, computed_value in computed.values.items():
        existing_value = existing.standardized_values.get(item_id)
        rows.append(
            ComparisonRow(
                item_id,
                existing_value,
                computed_value,
                0,
                computed_value,
                None if existing_value is None else computed_value - existing_value,
                computed.support_component_ids[item_id],
            )
        )
    return StatementComparison(tuple(rows))


def reconcile_cash(
    statement: StatementResult,
    opening_cent: int | None,
    closing_cent: int | None,
    fx_cent: int | None,
) -> ReconciliationResult:
    if opening_cent is None or closing_cent is None or fx_cent is None:
        return ReconciliationResult(
            "现金流量表与货币资金变动的勾稽核对：未完成", opening_cent, closing_cent, fx_cent, None, None
        )
    net_cash = (
        statement.values["CFO-NET"]
        + statement.values["CFI-NET"]
        + statement.values["CFF-NET"]
        + fx_cent
    )
    difference = closing_cent - opening_cent - net_cash
    status = "现金流量表与货币资金变动的勾稽核对：相符" if difference == 0 else "现金流量表与货币资金变动的勾稽核对：存在差异"
    return ReconciliationResult(
        status, opening_cent, closing_cent, fx_cent, net_cash, difference
    )
