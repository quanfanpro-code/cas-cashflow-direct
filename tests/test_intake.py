from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from cashflow_direct.intake import (
    UnreadableInputError,
    UnsupportedLegacyExcelError,
    choose_input_files,
    register_inputs,
    validate_materiality,
)


class IntakeTests(unittest.TestCase):
    def test_rejects_xls_with_save_as_xlsx_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "旧序时账.xls"
            old.write_bytes(b"legacy")
            with self.assertRaisesRegex(UnsupportedLegacyExcelError, "另存为.*xlsx"):
                register_inputs([old])

    def test_exact_duplicate_file_is_registered_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "明细1.xlsx"
            second = root / "明细2.xlsx"
            first.write_bytes(b"same")
            second.write_bytes(b"same")
            result = register_inputs([first, second], now=datetime(2026, 8, 4, 10, 30, 0))
            self.assertEqual(1, len(result.active_files))
            self.assertEqual(result.files[0].file_id, result.files[1].duplicate_of)
            self.assertNotEqual(first.parent, result.run_dir)

    def test_materiality_order_is_mandatory(self) -> None:
        valid = validate_materiality("1000000", "750000", "50000")
        self.assertEqual(5_000_000, valid.trivial_cent)
        with self.assertRaisesRegex(ValueError, "明显微小.*实际执行.*整体"):
            validate_materiality("100", "100", "5")

    def test_dialog_is_injected_and_empty_selection_is_allowed(self) -> None:
        selected = choose_input_files(lambda: ("C:/资料/明细.xlsx", "C:/资料/正表.xlsm"))
        self.assertEqual((Path("C:/资料/明细.xlsx"), Path("C:/资料/正表.xlsm")), selected)
        self.assertEqual((), choose_input_files(lambda: ()))

    def test_xlsm_is_read_only_and_input_hash_never_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            macro_book = Path(tmp) / "账簿.xlsm"
            macro_book.write_bytes(b"macro-content")
            before = hashlib.sha256(macro_book.read_bytes()).hexdigest()
            result = register_inputs([macro_book], now=datetime(2026, 8, 4, 10, 30, 0))
            self.assertTrue(result.files[0].is_macro_workbook)
            self.assertTrue(result.files[0].read_only)
            self.assertEqual(before, hashlib.sha256(macro_book.read_bytes()).hexdigest())

    def test_missing_and_unknown_files_are_rejected_with_business_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "缺失.xlsx"
            with self.assertRaisesRegex(UnreadableInputError, "缺失.xlsx.*无法读取"):
                register_inputs([missing])
            unknown = Path(tmp) / "明细.csv"
            unknown.write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(UnreadableInputError, "xlsx.*xlsm"):
                register_inputs([unknown])

    def test_run_directory_collision_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "明细.xlsx"
            source.write_bytes(b"same")
            moment = datetime(2026, 8, 4, 10, 30, 0)
            first = register_inputs([source], output_parent=root / "输出", now=moment)
            second = register_inputs([source], output_parent=root / "输出", now=moment)
            self.assertNotEqual(first.run_dir, second.run_dir)
            self.assertTrue(first.run_dir.is_dir())
            self.assertTrue(second.run_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
