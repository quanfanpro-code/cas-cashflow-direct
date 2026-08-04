from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from cashflow_direct.normalization import normalize_dataset
from cashflow_direct.semantic_mapping import DatasetMapping, infer_dataset_mapping
from cashflow_direct.workbook_structure import scan_workbook
from tests.fixture_factory import write_all_input_types


class NormalizationTests(unittest.TestCase):
    def test_five_input_shapes_normalize_without_template_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            files = write_all_input_types(Path(tmp))
            results = []
            for index, path in enumerate(files, start=1):
                mapping = infer_dataset_mapping(scan_workbook(path))
                self.assertIsInstance(mapping, DatasetMapping, path.name)
                results.append(normalize_dataset(path, f"F{index}", mapping))
            self.assertEqual([4, 5, 6, 4, 4], [len(result.entries) for result in results])
            self.assertTrue(results[0].profile.matched_counterparty)
            self.assertTrue(results[1].profile.has_flow_item)
            self.assertTrue(results[2].profile.split_duplication_risk)
            self.assertEqual(frozenset({"cash", "counterpart"}), results[3].profile.retained_side_values)
            self.assertTrue(results[4].profile.summary_only)
            for result in results:
                self.assertEqual(
                    result.rows_read,
                    len(result.entries) + len(result.exclusions) + len(result.errors),
                )
                for entry in result.entries:
                    self.assertTrue(entry.source.file_id)
                    self.assertTrue(entry.source.sheet_name)
                    self.assertGreater(entry.source.row_start, 0)
                    self.assertTrue(entry.source.cell_range)

    def test_invalid_money_is_a_located_error_not_silent_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "坏金额.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["发生额方向", "金额", "摘要", "现流项目"])
            sheet.append(["借", "无法识别", "匿名事项", "收到其他经营现金"])
            workbook.save(path)
            mapping = infer_dataset_mapping(scan_workbook(path))
            self.assertIsInstance(mapping, DatasetMapping)
            result = normalize_dataset(path, "FBAD", mapping)
            self.assertEqual(0, len(result.entries))
            self.assertEqual(1, len(result.errors))
            self.assertIn("B2", result.errors[0].source.cell_range)
            self.assertIn("金额", result.errors[0].message)


if __name__ == "__main__":
    unittest.main()
