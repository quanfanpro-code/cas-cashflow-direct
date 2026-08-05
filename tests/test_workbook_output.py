from __future__ import annotations

import tempfile
import unittest
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
    "现金范围与现金调节",
    "全量分类留痕",
    "输入识别与字段映射",
]


class WorkbookOutputTests(unittest.TestCase):
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
                                    self.assertEqual("Arial", cell.font.name)
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
                self.assertTrue(all(sheet.sheet_state == "visible" for sheet in workbook.worksheets))
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
        formula = manual_adjustment_formula("CFO-07", 2, 2)
        self.assertIn("重要待复核事项", formula)
        self.assertIn("疑似重复事项", formula)

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
                self.assertIn("CFI-09", validation)
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
                    "现金调节完成", 100_000, 160_000, 0, 60_000, 0
                ),
            )
            build_output_workbook(model, path)
            workbook = load_workbook(path, data_only=False)
            try:
                self.assertIn("现金流量表正表", workbook["正表核对报告"]["E2"].value)
                headers = [cell.value for cell in workbook["现金范围与现金调节"][1]]
                self.assertIn("金额（元）", headers)
                cash_sheet = workbook["现金范围与现金调节"]
                net_row = next(
                    row for row in range(2, cash_sheet.max_row + 1)
                    if cash_sheet.cell(row, 1).value == "本期现金净增加额"
                )
                difference_row = next(
                    row for row in range(2, cash_sheet.max_row + 1)
                    if cash_sheet.cell(row, 1).value == "现金调节差异"
                )
                self.assertIn("现金流量表正表", cash_sheet.cell(net_row, 3).value)
                self.assertTrue(str(cash_sheet.cell(difference_row, 3).value).startswith("="))
                self.assertIn("现金范围与现金调节", workbook["使用说明与状态"]["B3"].value)
                self.assertNotIn("101", str(workbook["全量分类留痕"].print_area))
            finally:
                workbook.close()


if __name__ == "__main__":
    unittest.main()
