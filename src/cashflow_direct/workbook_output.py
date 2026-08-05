from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import xlsxwriter
from openpyxl import load_workbook

from cashflow_direct.classification import RulePack
from cashflow_direct.duplicates import DuplicateGroup
from cashflow_direct.models import ReviewBatch
from cashflow_direct.statement import (
    ReconciliationResult,
    StatementComparison,
    StatementResult,
)


SHEET_NAMES = (
    "使用说明与状态",
    "现金流量表正表",
    "正表核对报告",
    "重要待复核事项",
    "疑似重复事项",
    "AI复核记录",
    "现金范围与现金调节",
    "全量分类留痕",
    "输入识别与字段映射",
)


@dataclass(frozen=True, slots=True)
class WorkbookModel:
    statement: StatementResult
    rules: RulePack
    comparison: StatementComparison | None
    review_batches: tuple[ReviewBatch, ...]
    duplicate_groups: tuple[DuplicateGroup, ...]
    ai_records: tuple[Mapping[str, object], ...]
    cash_scope_rows: tuple[Mapping[str, object], ...]
    reconciliation: ReconciliationResult | None
    trace_rows: tuple[Mapping[str, object], ...]
    mapping_rows: tuple[Mapping[str, object], ...]
    overall_status: str


@dataclass(frozen=True, slots=True)
class WorkbookValidation:
    valid: bool
    errors: tuple[str, ...]


def manual_adjustment_formula(
    item_id: str,
    review_last_row: int,
    duplicate_last_row: int,
) -> str:
    review_end = max(2, review_last_row)
    duplicate_end = max(2, duplicate_last_row)
    return (
        f'=SUMIFS(\'重要待复核事项\'!$F$2:$F${review_end},\'重要待复核事项\'!$C$2:$C${review_end},"{item_id}")'
        f'-SUMIFS(\'重要待复核事项\'!$F$2:$F${review_end},\'重要待复核事项\'!$B$2:$B${review_end},"{item_id}")'
        f'+SUMIFS(\'疑似重复事项\'!$F$2:$F${duplicate_end},\'疑似重复事项\'!$B$2:$B${duplicate_end},"{item_id}")'
    )


def calculate_manual_adjustments(
    model: WorkbookModel,
    review_decisions: Mapping[str, str],
    duplicate_decisions: Mapping[str, str],
) -> dict[str, int]:
    adjustments: defaultdict[str, int] = defaultdict(int)
    for batch in model.review_batches:
        selected = review_decisions.get(batch.batch_id, batch.proposed_item_code)
        if selected not in {"", "认可自动判断", batch.proposed_item_code}:
            amount = batch.baseline_statement_amount_cent
            adjustments[batch.proposed_item_code] -= amount
            adjustments[selected] += amount
    for group in model.duplicate_groups:
        if duplicate_decisions.get(group.group_id, group.default_decision) in {"exclude", "剔除"}:
            adjustments[group.item_id] -= group.baseline_statement_amount_cent
    return dict(adjustments)


def _formats(workbook: xlsxwriter.Workbook) -> dict[str, xlsxwriter.format.Format]:
    return {
        "title": workbook.add_format(
            {"bold": True, "font_size": 16, "font_color": "#FFFFFF", "bg_color": "#17365D", "align": "center", "valign": "vcenter"}
        ),
        "header": workbook.add_format(
            {"bold": True, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "border": 1, "align": "center", "valign": "vcenter"}
        ),
        "section": workbook.add_format({"bold": True, "bg_color": "#D9EAF7", "border": 1}),
        "text": workbook.add_format({"border": 1, "valign": "top"}),
        "money": workbook.add_format({"border": 1, "num_format": "#,##0.00;[Red](#,##0.00);-"}),
        "input": workbook.add_format({"border": 1, "bg_color": "#DDEBF7", "font_color": "#0070C0", "locked": False}),
        "pending": workbook.add_format({"border": 1, "bg_color": "#FFF2CC"}),
        "error": workbook.add_format({"border": 1, "bg_color": "#F4CCCC", "font_color": "#9C0006"}),
        "note": workbook.add_format({"italic": True, "font_color": "#666666"}),
        "link": workbook.add_format({"font_color": "#0563C1", "underline": True}),
    }


def _configure_sheet(
    sheet: xlsxwriter.worksheet.Worksheet, columns: int, rows: int = 2
) -> None:
    sheet.freeze_panes(1, 0)
    sheet.set_landscape()
    sheet.fit_to_pages(1, 0)
    sheet.print_area(0, 0, max(1, rows - 1), max(0, columns - 1))


def _write_dict_rows(
    sheet: xlsxwriter.worksheet.Worksheet,
    rows: Sequence[Mapping[str, object]],
    formats: dict[str, xlsxwriter.format.Format],
    empty_note: str,
) -> None:
    if not rows:
        sheet.write(0, 0, "说明", formats["header"])
        sheet.write(1, 0, empty_note, formats["note"])
        sheet.set_column(0, 0, 60)
        _configure_sheet(sheet, 1)
        return
    headers = tuple(dict.fromkeys(key for row in rows for key in row.keys()))
    for column, header in enumerate(headers):
        sheet.write(0, column, header, formats["header"])
    for row_index, row in enumerate(rows, 1):
        for column, header in enumerate(headers):
            sheet.write(row_index, column, row.get(header, ""), formats["text"])
    sheet.autofilter(0, 0, len(rows), len(headers) - 1)
    sheet.set_column(0, len(headers) - 1, 20)
    _configure_sheet(sheet, len(headers), len(rows) + 1)


def build_output_workbook(model: WorkbookModel, output_path: Path) -> Path:
    target = Path(output_path)
    if target.exists():
        raise FileExistsError(f"输出文件已存在，不会覆盖：{target}")
    if len(model.trace_rows) > 100_000:
        raise ValueError("全量分类留痕超过本版本 100,000 行验收范围")
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = xlsxwriter.Workbook(str(target))
    workbook.set_calc_mode("auto")
    formats = _formats(workbook)
    sheets = {name: workbook.add_worksheet(name) for name in SHEET_NAMES}
    try:
        status = sheets["使用说明与状态"]
        status.merge_range("A1:D1", "直接法现金流量表正表编制与复核底稿", formats["title"])
        status.write("A3", "当前状态", formats["header"])
        if model.overall_status.startswith("草稿"):
            status.write("B3", model.overall_status, formats["error"])
        elif model.review_batches or any(
            group.blocks_manual_completion for group in model.duplicate_groups
        ):
            review_end = max(2, len(model.review_batches) + 1)
            duplicate_end = max(2, len(model.duplicate_groups) + 1)
            pending_terms = []
            if model.review_batches:
                pending_terms.append(f"COUNTBLANK('重要待复核事项'!C2:C{review_end})")
            if any(group.blocks_manual_completion for group in model.duplicate_groups):
                pending_terms.append(
                    f'COUNTIFS(\'疑似重复事项\'!G2:G{duplicate_end},"是",'
                    f'\'疑似重复事项\'!C2:C{duplicate_end},"")'
                )
            status.write_formula(
                "B3",
                f'=IF({"+".join(pending_terms)}=0,"最终可使用","待完成人工确认")',
                formats["pending"],
                "待完成人工确认",
            )
        else:
            status.write("B3", "最终可使用", formats["text"])
        status.write("A5", "使用说明", formats["header"])
        status.write("B5", "蓝色单元格为人工选择区；修改后正表会即时更新，无需再次运行本工具。", formats["text"])
        for index, name in enumerate(SHEET_NAMES[1:], 7):
            status.write_url(index - 1, 0, f"internal:'{name}'!A1", formats["link"], string=name)
        status.set_column("A:A", 24)
        status.set_column("B:D", 32)
        status.freeze_panes(2, 0)
        status.print_area("A1:D20")

        review = sheets["重要待复核事项"]
        review_headers = (
            "批次编号",
            "系统项目",
            "人工确认项目",
            "最不利影响金额",
            "系统基线金额",
            "有效调整金额",
            "原因",
            "状态",
            "包含笔数",
            "业务组成编号",
            "摘要模式",
            "对方科目组",
        )
        for column, header in enumerate(review_headers):
            review.write(0, column, header, formats["header"])
        if model.review_batches:
            for row_index, batch in enumerate(model.review_batches, 1):
                review.write(row_index, 0, batch.batch_id, formats["text"])
                review.write(row_index, 1, batch.proposed_item_code, formats["text"])
                review.write_blank(row_index, 2, None, formats["input"])
                review.write_number(row_index, 3, batch.worst_case_impact_cent / 100, formats["money"])
                review.write_number(
                    row_index,
                    4,
                    batch.baseline_statement_amount_cent / 100,
                    formats["money"],
                )
                review.write_formula(
                    row_index,
                    5,
                    f'=IF(OR(C{row_index + 1}="",C{row_index + 1}="认可自动判断"),0,E{row_index + 1})',
                    formats["money"],
                    0,
                )
                review.write(row_index, 6, batch.reason, formats["text"])
                review.write_formula(
                    row_index,
                    7,
                    f'=IF(C{row_index + 1}="","待确认",IF(C{row_index + 1}="认可自动判断","认可自动判断","已改列"))',
                    formats["pending"],
                    "待确认",
                )
                review.write_number(row_index, 8, len(batch.component_ids), formats["text"])
                review.write(row_index, 9, "、".join(batch.component_ids), formats["text"])
                review.write(row_index, 10, batch.representative_summary, formats["text"])
                review.write(row_index, 11, batch.counterpart_group, formats["text"])
                review.data_validation(
                    row_index,
                    2,
                    row_index,
                    2,
                    {
                        "validate": "list",
                        "source": ["认可自动判断", *batch.alternative_item_codes],
                    },
                )
            review.autofilter(0, 0, len(model.review_batches), len(review_headers) - 1)
        else:
            review.write(1, 0, "本期无重大剩余不确定事项，无需人工复核。", formats["note"])
        review.set_column("A:C", 18)
        review.set_column("D:F", 16)
        review.set_column("G:G", 46)
        review.set_column("H:H", 16)
        review.set_column("I:I", 12)
        review.set_column("J:L", 32)
        _configure_sheet(review, len(review_headers), len(model.review_batches) + 1)
        review.protect("", {"autofilter": True, "sort": True, "select_unlocked_cells": True})

        duplicate = sheets["疑似重复事项"]
        duplicate_headers = (
            "组编号",
            "标准项目",
            "人工决定",
            "最不利影响金额",
            "重复计入正表金额",
            "有效调整金额",
            "是否阻止完成",
        )
        for column, header in enumerate(duplicate_headers):
            duplicate.write(0, column, header, formats["header"])
        if model.duplicate_groups:
            for row_index, group in enumerate(model.duplicate_groups, 1):
                duplicate.write(row_index, 0, group.group_id, formats["text"])
                duplicate.write(row_index, 1, group.item_id, formats["text"])
                duplicate.write_blank(row_index, 2, None, formats["input"])
                duplicate.write_number(row_index, 3, group.worst_case_impact_cent / 100, formats["money"])
                duplicate.write_number(
                    row_index,
                    4,
                    group.baseline_statement_amount_cent / 100,
                    formats["money"],
                )
                duplicate.write_formula(
                    row_index,
                    5,
                    f'=IF(C{row_index + 1}="剔除",-E{row_index + 1},0)',
                    formats["money"],
                    0,
                )
                duplicate.write(row_index, 6, "是" if group.blocks_manual_completion else "否", formats["pending"])
                duplicate.data_validation(row_index, 2, row_index, 2, {"validate": "list", "source": ["保留", "剔除"]})
            duplicate.autofilter(0, 0, len(model.duplicate_groups), len(duplicate_headers) - 1)
        else:
            duplicate.write(1, 0, "本期未发现跨文件疑似重复事项。", formats["note"])
        duplicate.set_column("A:C", 18)
        duplicate.set_column("D:G", 18)
        _configure_sheet(duplicate, len(duplicate_headers), len(model.duplicate_groups) + 1)
        duplicate.protect("", {"autofilter": True, "sort": True, "select_unlocked_cells": True})

        main = sheets["现金流量表正表"]
        main.merge_range("A1:F1", "现金流量表", formats["title"])
        main.write("A2", "金额单位：人民币元", formats["note"])
        headers = ("项目编号", "项目", "上期金额", "自动基线", "人工调整", "最终金额")
        for column, header in enumerate(headers):
            main.write(2, column, header, formats["header"])
        ordered = sorted(model.rules.statement_items, key=lambda item: item.display_order)
        excel_row_by_id = {item.item_id: index + 4 for index, item in enumerate(ordered)}
        review_last = max(2, len(model.review_batches) + 1)
        duplicate_last = max(2, len(model.duplicate_groups) + 1)
        for index, item in enumerate(ordered):
            row = index + 3
            excel_row = row + 1
            row_format = formats["text"] if item.is_leaf else formats["section"]
            main.write(row, 0, item.item_id, row_format)
            main.write(row, 1, item.name, row_format)
            prior = model.statement.prior_values.get(item.item_id)
            if prior is not None:
                main.write_number(row, 2, prior / 100, formats["money"])
            else:
                main.write_blank(row, 2, None, formats["money"])
            main.write_number(row, 3, model.statement.values[item.item_id] / 100, formats["money"])
            if item.is_leaf:
                main.write_formula(
                    row,
                    4,
                    manual_adjustment_formula(item.item_id, review_last, duplicate_last),
                    formats["money"],
                    0,
                )
            else:
                main.write_number(row, 4, 0, formats["money"])
            if item.formula_components:
                parts = [
                    f"F{excel_row_by_id[source_id]}*{multiplier}"
                    for source_id, multiplier in item.formula_components
                ]
                formula = "=" + "+".join(parts).replace("+-", "-")
            else:
                formula = f"=D{excel_row}+E{excel_row}"
            main.write_formula(
                row,
                5,
                formula,
                formats["money"],
                model.statement.values[item.item_id] / 100,
            )
        main.freeze_panes(3, 0)
        main.autofilter(2, 0, len(ordered) + 2, len(headers) - 1)
        main.set_column("A:A", 16)
        main.set_column("B:B", 50)
        main.set_column("C:F", 18)
        main.set_landscape()
        main.fit_to_pages(1, 1)
        main.print_area(0, 0, len(ordered) + 2, 5)
        main.protect("", {"autofilter": True, "sort": True})

        comparison_rows = () if model.comparison is None else tuple(
            {
                "项目编号": row.item_id,
                "客户金额": None if row.existing_cent is None else row.existing_cent / 100,
                "自动基线": row.computed_cent / 100,
                "人工调整": row.manual_adjustment_cent / 100,
                "最终金额": row.final_cent / 100,
                "差异": None if row.difference_cent is None else row.difference_cent / 100,
                "支持组成": "、".join(row.support_component_ids),
            }
            for row in model.comparison.rows
        )
        comparison_sheet = sheets["正表核对报告"]
        if comparison_rows:
            comparison_headers = ("项目编号", "客户金额", "自动基线", "人工调整", "最终金额", "差异", "支持组成")
            for column, header in enumerate(comparison_headers):
                comparison_sheet.write(0, column, header, formats["header"])
            for row_index, row in enumerate(comparison_rows, 1):
                main_row = row_index + 3
                comparison_sheet.write(row_index, 0, row["项目编号"], formats["text"])
                if row["客户金额"] is None:
                    comparison_sheet.write_blank(row_index, 1, None, formats["money"])
                else:
                    comparison_sheet.write_number(row_index, 1, row["客户金额"], formats["money"])
                comparison_sheet.write_formula(row_index, 2, f"='现金流量表正表'!D{main_row}", formats["money"], row["自动基线"])
                comparison_sheet.write_formula(row_index, 3, f"='现金流量表正表'!E{main_row}", formats["money"], row["人工调整"])
                comparison_sheet.write_formula(row_index, 4, f"='现金流量表正表'!F{main_row}", formats["money"], row["最终金额"])
                comparison_sheet.write_formula(row_index, 5, f"=IF(B{row_index + 1}=\"\",\"\",E{row_index + 1}-B{row_index + 1})", formats["money"], row["差异"])
                comparison_sheet.write(row_index, 6, row["支持组成"], formats["text"])
            comparison_sheet.autofilter(0, 0, len(comparison_rows), len(comparison_headers) - 1)
            comparison_sheet.set_column("A:A", 16)
            comparison_sheet.set_column("B:F", 18)
            comparison_sheet.set_column("G:G", 40)
            _configure_sheet(comparison_sheet, len(comparison_headers), len(comparison_rows) + 1)
            comparison_sheet.protect("", {"autofilter": True, "sort": True})
        else:
            _write_dict_rows(comparison_sheet, (), formats, "本次为编制任务，未提供客户现有正表。")
        _write_dict_rows(sheets["AI复核记录"], model.ai_records, formats, "本期没有需要 AI 复核的事项。")

        cash_rows = list(model.cash_scope_rows)
        if model.reconciliation is not None:
            for project, amount in (
                ("期初现金及现金等价物余额", model.reconciliation.opening_cent),
                ("汇率变动影响", model.reconciliation.fx_cent),
                ("本期现金净增加额", model.reconciliation.net_cash_cent),
                ("期末现金及现金等价物余额", model.reconciliation.closing_cent),
                ("现金调节差异", model.reconciliation.difference_cent),
            ):
                cash_rows.append(
                    {
                        "科目": project,
                        "决定": model.reconciliation.status,
                        "金额（元）": None if amount is None else amount / 100,
                    }
                )
        _write_dict_rows(sheets["现金范围与现金调节"], tuple(cash_rows), formats, "现金范围尚未确认。")
        _write_dict_rows(sheets["全量分类留痕"], model.trace_rows, formats, "没有现金流业务组成。")
        _write_dict_rows(sheets["输入识别与字段映射"], model.mapping_rows, formats, "没有字段映射记录。")
    finally:
        workbook.close()
    return target


def validate_output_workbook(path: Path, model: WorkbookModel) -> WorkbookValidation:
    errors: list[str] = []
    workbook = load_workbook(path, data_only=False, keep_links=True)
    try:
        if tuple(workbook.sheetnames) != SHEET_NAMES:
            errors.append("工作表名称或顺序不正确")
        if any(sheet.sheet_state != "visible" for sheet in workbook.worksheets):
            errors.append("存在隐藏的关键工作表")
        if workbook._external_links:
            errors.append("工作簿包含外部链接")
        main = workbook["现金流量表正表"]
        if main.freeze_panes != "A4":
            errors.append("正表冻结窗格不正确")
        if main.auto_filter.ref is None:
            errors.append("正表未设置筛选")
        formulas = [
            cell.value
            for row in main.iter_rows()
            for cell in row
            if isinstance(cell.value, str) and cell.value.startswith("=")
        ]
        if not any("重要待复核事项" in formula for formula in formulas):
            errors.append("正表未引用重要待复核事项调整层")
        if not any("疑似重复事项" in formula for formula in formulas):
            errors.append("正表未引用疑似重复事项调整层")
        if any("[" in formula or "全量分类留痕" in formula for formula in formulas):
            errors.append("正表公式引用了外部工作簿或全量留痕")
        for index, item in enumerate(sorted(model.rules.statement_items, key=lambda value: value.display_order), 4):
            actual = main.cell(index, 4).value
            expected = model.statement.values[item.item_id] / 100
            if actual != expected:
                errors.append(f"自动基线不一致：{item.item_id}")
                break
        if model.review_batches and not workbook["重要待复核事项"].data_validations.dataValidation:
            errors.append("重要待复核事项缺少下拉选择")
        if model.duplicate_groups and not workbook["疑似重复事项"].data_validations.dataValidation:
            errors.append("疑似重复事项缺少下拉选择")
        if model.review_batches:
            fill = workbook["重要待复核事项"]["C2"].fill.fgColor.rgb
            if fill not in {"FFDDEBF7", "DDEBF7"}:
                errors.append("人工输入单元格未使用蓝色标识")
    finally:
        workbook.close()
    return WorkbookValidation(not errors, tuple(errors))
