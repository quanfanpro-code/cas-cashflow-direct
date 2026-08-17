from __future__ import annotations

import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook

from cashflow_direct.workbook_output import (
    build_output_workbook,
    calculate_manual_adjustments,
    manual_adjustment_formula,
    validate_output_workbook,
)
from cashflow_direct.statement import (
    ExistingStatementResult,
    ReconciliationResult,
    compare_statement,
)
from cashflow_direct.models import ReviewBatch
from tests.fixture_factory import workbook_model


EXPECTED_SHEETS = [
    "使用说明与状态",
    "现金流量表正表",
    "正表核对报告",
    "重要待复核事项",
    "疑似重复事项",
    "AI复核记录",
    "原表与自动判定差异",
    "现金范围与现金流量表与货币资金变动的勾稽核对",
    "全量分类留痕",
    "输入识别与字段映射",
]


class WorkbookOutputTests(unittest.TestCase):
    def test_generic_money_columns_use_thousands_separators(self) -> None:
        model = replace(
            workbook_model(0, 0),
            reconciliation=ReconciliationResult(
                "现金流量表与货币资金变动的勾稽核对：相符",
                123_456_789,
                133_456_789,
                0,
                10_000_000,
                0,
            ),
            trace_rows=({"摘要": "匿名业务", "现金变化": 1_234_567.89},),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "金额格式.xlsx"
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                cash = workbook["现金范围与现金流量表与货币资金变动的勾稽核对"]
                amount_column = [cell.value for cell in cash[1]].index("金额（元）") + 1
                opening_row = next(
                    row
                    for row in range(2, cash.max_row + 1)
                    if cash.cell(row, 1).value == "期初现金及现金等价物余额"
                )
                self.assertIn(
                    "#,##0.00", cash.cell(opening_row, amount_column).number_format
                )
                self.assertIn(
                    "#,##0.00", workbook["全量分类留痕"]["B2"].number_format
                )
            finally:
                workbook.close()

    def test_original_auto_difference_sheet_keeps_rows_visible_and_read_only(self) -> None:
        difference_row = {
            "日期": "2026-01-01",
            "凭证字": "记",
            "凭证号": "1",
            "摘要": "匿名税费",
            "科目编码": "1002.01",
            "科目名称": "银行存款",
            "借方": None,
            "贷方": 100.0,
            "流量金额（原币）": 100.0,
            "主表项目名称": "支付的各项税费",
            "对方科目": "营业外支出",
            "原项目标准化结果": "支付的各项税费",
            "自动判定现流项目": "支付其他与经营活动有关的现金",
            "差异说明": "标准项目不一致",
            "来源文件": "匿名输入.xlsx",
            "来源工作表": "明细",
            "来源单元格": "A2:L2",
        }
        model = replace(workbook_model(0, 0), difference_rows=(difference_row,))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "原表差异.xlsx"
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                sheet = workbook["原表与自动判定差异"]
                self.assertEqual(
                    list(difference_row), [cell.value for cell in sheet[1]]
                )
                self.assertEqual("A2", sheet.freeze_panes)
                self.assertIsNotNone(sheet.auto_filter.ref)
                self.assertEqual("支付的各项税费", sheet["J2"].value)
                self.assertEqual(
                    "支付其他与经营活动有关的现金", sheet["M2"].value
                )
                self.assertIn("#,##0.00", sheet["H2"].number_format)
                self.assertIsNone(sheet["G2"].value)
                self.assertTrue(sheet["A2"].protection.locked)
                self.assertTrue(sheet.protection.sheet)
            finally:
                workbook.close()

    def test_empty_difference_sheet_remains_visible_with_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "无差异.xlsx"
            build_output_workbook(workbook_model(0, 0), path)
            workbook = load_workbook(path, data_only=False)
            try:
                sheet = workbook["原表与自动判定差异"]
                self.assertEqual("visible", sheet.sheet_state)
                self.assertIn("无差异", str(sheet["A2"].value))
            finally:
                workbook.close()

    def test_more_than_100000_difference_rows_are_rejected(self) -> None:
        row = {"主表项目名称": "支付的各项税费", "自动判定现流项目": "不进入正表"}
        model = replace(workbook_model(0, 0), difference_rows=(row,) * 100_001)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "差异明细超过"):
                build_output_workbook(model, Path(tmp) / "超限.xlsx")

    def test_generated_workbook_uses_consistent_professional_base_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "统一格式.xlsx"
            build_output_workbook(workbook_model(1, 1), path)
            workbook = load_workbook(path, data_only=False)
            try:
                for sheet in workbook.worksheets:
                    with self.subTest(sheet=sheet.title):
                        self.assertFalse(sheet.sheet_view.showGridLines)
                        self.assertEqual(18, sheet.sheet_format.defaultRowHeight)
                        for row in sheet.iter_rows():
                            for cell in row:
                                if cell.value not in (None, ""):
                                    self.assertEqual("Times New Roman", cell.font.name)
                with zipfile.ZipFile(path) as package:
                    theme = package.read("xl/theme/theme1.xml").decode("utf-8")
                self.assertIn('script="Hans" typeface="宋体"', theme)
                self.assertNotIn("MS Gothic", theme)
            finally:
                workbook.close()

    def test_workbook_has_expected_visible_sheets_and_no_external_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "现金流量表正表及复核底稿.xlsx"
            model = workbook_model(review_batches=1, duplicate_groups=1)
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False, keep_links=True)
            try:
                self.assertEqual(EXPECTED_SHEETS, workbook.sheetnames)
                hidden = {
                    sheet.title
                    for sheet in workbook.worksheets
                    if sheet.sheet_state == "hidden"
                }
                self.assertEqual({"AI复核记录", "输入识别与字段映射"}, hidden)
                self.assertEqual([], workbook._external_links)
            finally:
                workbook.close()
            validation = validate_output_workbook(path, model)
            self.assertTrue(validation.valid, validation.errors)

    def test_manual_formula_references_only_small_adjustment_sheets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "底稿.xlsx"
            build_output_workbook(workbook_model(review_batches=2, duplicate_groups=2), path)
            workbook = load_workbook(path, data_only=False)
            try:
                formulas = [
                    cell.value
                    for row in workbook["现金流量表正表"].iter_rows()
                    for cell in row
                    if isinstance(cell.value, str) and cell.value.startswith("=")
                ]
                self.assertTrue(any("重要待复核事项" in formula for formula in formulas))
                self.assertTrue(any("疑似重复事项" in formula for formula in formulas))
                self.assertTrue(all("全量分类留痕" not in formula for formula in formulas))
                self.assertTrue(all("原表与自动判定差异" not in formula for formula in formulas))
                self.assertLess(len(formulas), 300)
                self.assertTrue(workbook["现金流量表正表"].freeze_panes)
                self.assertTrue(workbook["重要待复核事项"].data_validations.dataValidation)
            finally:
                workbook.close()

    def test_review_reclassification_and_duplicate_exclusion_adjust_once(self) -> None:
        model = workbook_model(review_batches=1, duplicate_groups=1)
        adjustments = calculate_manual_adjustments(
            model,
            review_decisions={"REV-1": "CFI-09"},
            duplicate_decisions={"DUP-1": "exclude"},
        )
        self.assertEqual(-10_000, adjustments["CFO-07"])
        self.assertEqual(10_000, adjustments["CFI-09"])
        self.assertEqual(-20_000, adjustments["CFO-03"])
        formula = manual_adjustment_formula("支付其他与经营活动有关的现金", 2, 2)
        self.assertIn("重要待复核事项", formula)
        self.assertIn("疑似重复事项", formula)
        self.assertIn("支付其他与经营活动有关的现金", formula)
        self.assertNotIn("CFO-07", formula)

    def test_invalid_pasted_review_text_is_neutral_and_cannot_complete_status(self) -> None:
        model = workbook_model(review_batches=1, duplicate_groups=0)
        self.assertEqual(
            {},
            calculate_manual_adjustments(model, {"REV-1": "随意粘贴的无效项目"}, {}),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "无效选择防护.xlsx"
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                review_formulas = [
                    cell.value
                    for row in workbook["重要待复核事项"].iter_rows()
                    for cell in row
                    if isinstance(cell.value, str) and cell.value.startswith("=")
                ]
                self.assertTrue(any("无效选择" in formula for formula in review_formulas))
                self.assertIn("无效选择", workbook["使用说明与状态"]["B3"].value)
            finally:
                workbook.close()

    def test_reverse_direction_review_uses_signed_statement_amount(self) -> None:
        model = replace(
            workbook_model(0, 0),
            review_batches=(
                ReviewBatch(
                    "REV-REFUND",
                    ("C-REFUND",),
                    "CFO-04",
                    ("CFO-03",),
                    10_000,
                    "退款分类仍不确定",
                    baseline_statement_amount_cent=-10_000,
                    cash_delta_cent=10_000,
                ),
            ),
        )
        adjustments = calculate_manual_adjustments(
            model, {"REV-REFUND": "CFO-03"}, {}
        )
        self.assertEqual(10_000, adjustments["CFO-04"])
        self.assertEqual(10_000, adjustments["CFO-03"])

    def test_zero_review_batches_show_clear_note_and_statement_still_builds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "无重大事项.xlsx"
            build_output_workbook(workbook_model(0, 0), path)
            workbook = load_workbook(path, data_only=False)
            try:
                self.assertIn("无重大", workbook["重要待复核事项"]["A2"].value)
                self.assertEqual(35, workbook["现金流量表正表"].max_row - 3)
            finally:
                workbook.close()

    def test_manual_cells_are_narrowly_validated_and_formulas_are_protected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "受保护底稿.xlsx"
            model = workbook_model(1, 1)
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                status_formula = workbook["使用说明与状态"]["B3"].value
                self.assertIn("待确认", status_formula)
                self.assertIn("无效选择", status_formula)
                review = workbook["重要待复核事项"]
                self.assertIsNone(review["C2"].value)
                self.assertFalse(review["C2"].protection.locked)
                validation = review.data_validations.dataValidation[0].formula1
                self.assertIn("认可自动判断", validation)
                self.assertIn("支付其他与投资活动有关的现金", validation)
                self.assertNotIn("CFI-09", validation)
                self.assertNotIn("CFO-01", validation)
                self.assertTrue(review.protection.sheet)
                self.assertTrue(workbook["现金流量表正表"].protection.sheet)
            finally:
                workbook.close()

    def test_comparison_and_reconciliation_follow_final_statement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "动态核对底稿.xlsx"
            model = workbook_model(1, 0)
            existing = ExistingStatementResult(
                values=dict(model.statement.values),
                prior_values=dict(model.statement.prior_values),
                standardized_values=dict(model.statement.values),
                custom_rows=(),
                unit_multiplier=1,
            )
            model = replace(
                model,
                comparison=compare_statement(existing, model.statement),
                reconciliation=ReconciliationResult(
                    "现金流量表与货币资金变动的勾稽核对：相符", 100_000, 160_000, 0, 60_000, 0
                ),
            )
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                self.assertIn("现金流量表正表", workbook["正表核对报告"]["E2"].value)
                headers = [cell.value for cell in workbook["现金范围与现金流量表与货币资金变动的勾稽核对"][1]]
                self.assertIn("金额（元）", headers)
                cash_sheet = workbook["现金范围与现金流量表与货币资金变动的勾稽核对"]
                net_row = next(
                    row for row in range(2, cash_sheet.max_row + 1)
                    if cash_sheet.cell(row, 1).value == "本期现金净增加额"
                )
                difference_row = next(
                    row for row in range(2, cash_sheet.max_row + 1)
                    if cash_sheet.cell(row, 1).value == "勾稽差异"
                )
                self.assertIn("现金流量表正表", cash_sheet.cell(net_row, 3).value)
                self.assertTrue(str(cash_sheet.cell(difference_row, 3).value).startswith("="))
                self.assertIn("ROUND", cash_sheet.cell(difference_row, 3).value)
                self.assertIn("ROUND", workbook["正表核对报告"]["F2"].value)
                self.assertIn("现金范围与现金流量表与货币资金变动的勾稽核对", workbook["使用说明与状态"]["B3"].value)
                self.assertNotIn("101", str(workbook["全量分类留痕"].print_area))
            finally:
                workbook.close()


    def test_human_sheets_show_names_and_hide_machine_columns(self) -> None:
        model = replace(
            workbook_model(1, 1),
            ai_records=(
                {
                    "阶段": "首次复核",
                    "task_id": "TASK-1",
                    "component_id": "COMP-1",
                    "item_id": "CFO-03",
                    "reason": "AI 与自动判断一致",
                    "confidence": "high",
                },
            ),
            trace_rows=(
                {
                    "记录类型": "现金流业务组成",
                    "摘要": "匿名业务",
                    "现金变化": 100.0,
                    "原现流项目": "支付其他与经营活动有关的现金",
                    "对方科目": "普通往来科目",
                    "自动判定现流项目": "支付其他与经营活动有关的现金",
                    "判断理由": "命中规则",
                    "证据强度": "high",
                    "异常": "",
                    "方向依据": "借贷差额",
                    "来源文件": "匿名输入.xlsx",
                    "来源工作表": "匿名数据",
                    "来源单元格": "A1:H1",
                    "一致性复核状态": "重大一致性复核已收口",
                    "一致性复核理由": "整组业务实质一致",
                    "一致性重要性层级": "达到整体重要性",
                    "决策来源(技术)": "system",
                    "命中规则(技术)": "CFO-07-FALLBACK",
                    "业务组成编号(技术)": "C-1",
                    "来源占用键(技术)": "E-1",
                    "业务组编号(技术)": "CGR-1",
                },
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "留痕分层.xlsx"
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                headers = [cell.value for cell in workbook["全量分类留痕"][1]]
                self.assertEqual(
                    [
                        "记录类型", "摘要", "现金变化", "原现流项目", "对方科目", "自动判定现流项目",
                        "判断理由", "证据强度", "异常", "方向依据", "来源文件", "来源工作表",
                        "来源单元格", "一致性复核状态", "一致性复核理由", "一致性重要性层级",
                        "决策来源(技术)", "命中规则(技术)", "业务组成编号(技术)",
                        "来源占用键(技术)", "业务组编号(技术)",
                    ],
                    headers,
                )
                self.assertEqual(
                    "支付其他与经营活动有关的现金",
                    workbook["全量分类留痕"]["F2"].value,
                )
                trace = workbook["全量分类留痕"]
                self.assertFalse(trace.column_dimensions["N"].hidden)
                self.assertFalse(trace.column_dimensions["Q"].hidden)
                for header in (
                    "命中规则(技术)",
                    "业务组成编号(技术)",
                    "来源占用键(技术)",
                    "业务组编号(技术)",
                ):
                    column_index = headers.index(header) + 1
                    self.assertTrue(
                        any(
                            dimension.hidden
                            and dimension.min <= column_index <= dimension.max
                            for dimension in trace.column_dimensions.values()
                        )
                    )

                review = workbook["重要待复核事项"]
                self.assertEqual("支付其他与经营活动有关的现金", review["B2"].value)
                self.assertTrue(review.column_dimensions["A"].hidden)
                self.assertTrue(review.column_dimensions["L"].hidden)
                self.assertNotIn("OR(FALSE)", review["J2"].value)

                duplicate = workbook["疑似重复事项"]
                self.assertEqual("收到其他与经营活动有关的现金", duplicate["B2"].value)

                ai_sheet = workbook["AI复核记录"]
                ai_headers = [cell.value for cell in ai_sheet[1]]
                self.assertIn("现流项目", ai_headers)
                item_column = ai_headers.index("现流项目") + 1
                self.assertEqual(
                    "收到其他与经营活动有关的现金",
                    ai_sheet.cell(2, item_column).value,
                )
            finally:
                workbook.close()

    def test_comparison_uses_full_project_name_and_hides_support_column(self) -> None:
        model = workbook_model(0, 0)
        existing = ExistingStatementResult(
            values=dict(model.statement.values),
            prior_values=dict(model.statement.prior_values),
            standardized_values=dict(model.statement.values),
            custom_rows=(),
            unit_multiplier=1,
        )
        model = replace(model, comparison=compare_statement(existing, model.statement))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "人类可读核对报告.xlsx"
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                sheet = workbook["正表核对报告"]
                self.assertEqual("项目", sheet["A1"].value)
                self.assertEqual(model.rules.statement_items[0].name, sheet["A2"].value)
                self.assertTrue(sheet.column_dimensions["G"].hidden)
            finally:
                workbook.close()


    def test_new_workbook_never_contains_legacy_reconciliation_term(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "无旧词.xlsx"
            model = workbook_model(0, 0)
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                hits = [
                    f"{sheet.title}!{cell.coordinate}"
                    for sheet in workbook.worksheets
                    for row in sheet.iter_rows()
                    for cell in row
                    if isinstance(cell.value, str) and "现金调节" in cell.value
                ]
                self.assertEqual([], hits)
            finally:
                workbook.close()


if __name__ == "__main__":

    unittest.main()
