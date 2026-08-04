from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from cashflow_direct.workbook_output import (
    build_output_workbook,
    calculate_manual_adjustments,
    manual_adjustment_formula,
    validate_output_workbook,
)
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


if __name__ == "__main__":
    unittest.main()
