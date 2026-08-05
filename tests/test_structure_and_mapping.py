from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from cashflow_direct.semantic_mapping import (
    DatasetMapping,
    MappingQuestion,
    infer_dataset_mapping,
    infer_dataset_mappings,
)
from cashflow_direct.workbook_structure import find_header_bands, scan_workbook
from tests.fixture_factory import (
    write_ambiguous_money_fixture,
    write_complex_header_fixture,
    write_hostile_header_fixture,
)


class StructureAndMappingTests(unittest.TestCase):
    def test_every_data_sheet_gets_its_own_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "按月序时账.xlsx"
            workbook = Workbook()
            for index, title in enumerate(("一月", "二月")):
                sheet = workbook.active if index == 0 else workbook.create_sheet()
                sheet.title = title
                sheet.append(["日期", "凭证号", "摘要", "科目", "借方", "贷方"])
                sheet.append([f"2026-0{index + 1}-01", "记-1", "匿名收款", "银行存款", 100, None])
            workbook.save(path)
            mappings = infer_dataset_mappings(scan_workbook(path))
            self.assertEqual(
                {"一月", "二月"},
                {item.sheet_name for item in mappings if isinstance(item, DatasetMapping)},
            )

    def test_merged_multiline_header_has_same_roles_at_different_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "结构甲.xlsx"
            second = Path(tmp) / "结构乙.xlsx"
            write_complex_header_fixture(first, header_row=2, label_side="cash")
            write_complex_header_fixture(second, header_row=9, label_side="counterpart")
            snapshot_a = scan_workbook(first)
            snapshot_b = scan_workbook(second)
            mapping_a = infer_dataset_mapping(snapshot_a)
            mapping_b = infer_dataset_mapping(snapshot_b)
            self.assertIsInstance(mapping_a, DatasetMapping)
            self.assertIsInstance(mapping_b, DatasetMapping)
            required = {
                "voucher_date",
                "voucher_no",
                "summary",
                "account_name",
                "debit",
                "credit",
                "flow_item",
            }
            self.assertTrue(required.issubset(mapping_a.role_to_column))
            self.assertTrue(required.issubset(mapping_b.role_to_column))
            self.assertNotEqual(mapping_a.header_row_start, mapping_b.header_row_start)
            self.assertTrue(snapshot_a.sheets[0].merged_ranges)
            self.assertTrue(find_header_bands(snapshot_a))
            self.assertEqual("E", mapping_a.role_to_column["debit"].column_letter)
            self.assertIn("发生额", mapping_a.role_to_column["debit"].header_path)

    def test_close_semantic_candidates_return_question_instead_of_guessing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "歧义.xlsx"
            write_ambiguous_money_fixture(path)
            result = infer_dataset_mapping(scan_workbook(path))
            self.assertIsInstance(result, MappingQuestion)
            self.assertEqual("debit", result.role)
            self.assertEqual(3, len(result.sample_values))
            self.assertTrue(result.recommended.header_path)
            self.assertTrue(result.alternatives)

    def test_three_level_header_and_hostile_layout_keep_full_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "恶劣结构.xlsx"
            write_hostile_header_fixture(path)
            snapshot = scan_workbook(path)
            result = infer_dataset_mapping(snapshot)
            self.assertIsInstance(result, DatasetMapping)
            self.assertEqual(5, result.header_row_start)
            self.assertEqual(7, result.header_row_end)
            self.assertEqual((8,), snapshot.sheets[0].hidden_columns)
            self.assertEqual(
                ("凭证及现金流数据", "发生额", "借方"),
                result.role_to_column["debit"].header_path,
            )


if __name__ == "__main__":
    unittest.main()
