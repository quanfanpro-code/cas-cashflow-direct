from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from cashflow_direct.models import MaterialityAmounts, SourceLocator, validate_materiality_order
from cashflow_direct.money import stable_id, statement_amount_cent, yuan_to_cent
from cashflow_direct.storage import RunStore


EXPECTED_TABLES = {
    "ai_task",
    "cash_scope",
    "cashflow_component",
    "classification_decision",
    "duplicate_group",
    "field_mapping",
    "reconciliation",
    "review_batch",
    "run_event",
    "run_manifest",
    "sheet_structure",
    "source_entry",
    "source_file",
    "stage_status",
    "statement_comparison",
    "statement_value",
    "voucher",
}


class MoneyAndStorageTests(unittest.TestCase):
    def test_decimal_money_never_uses_binary_float_accumulation(self) -> None:
        self.assertEqual(10, yuan_to_cent("0.10"))
        self.assertEqual(30, yuan_to_cent("0.1") + yuan_to_cent("0.2"))
        self.assertEqual(-20, statement_amount_cent(20, "outflow"))
        self.assertEqual(100, statement_amount_cent(-100, "outflow"))
        for invalid in (None, "", True, "NaN", "Infinity"):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "金额"):
                    yuan_to_cent(invalid)
        with self.assertRaisesRegex(ValueError, "方向"):
            statement_amount_cent(1, "sideways")

    def test_stable_id_is_repeatable_and_namespaced(self) -> None:
        first = stable_id("CMP", "F1", "S1", 3)
        self.assertEqual(first, stable_id("CMP", "F1", "S1", 3))
        self.assertTrue(first.startswith("CMP_"))
        self.assertNotEqual(first, stable_id("ENT", "F1", "S1", 3))

    def test_business_models_are_immutable_and_materiality_is_ordered(self) -> None:
        locator = SourceLocator("F1", "序时账", 3, 3, "A3:H3")
        with self.assertRaises(FrozenInstanceError):
            locator.row_start = 4  # type: ignore[misc]
        valid = MaterialityAmounts(100_000, 75_000, 5_000)
        self.assertIs(valid, validate_materiality_order(valid))
        for invalid in (
            MaterialityAmounts(100, 100, 5),
            MaterialityAmounts(100, 50, 50),
            MaterialityAmounts(50, 100, 5),
            MaterialityAmounts(100, 50, 0),
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "明显微小.*实际执行.*整体"):
                    validate_materiality_order(invalid)

    def test_failed_stage_rolls_back_and_does_not_claim_completion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp) / "trace.sqlite3")
            store.initialize()
            with self.assertRaises(RuntimeError):
                with store.stage("normalize") as connection:
                    connection.execute(
                        "INSERT INTO run_event(stage, message) VALUES (?, ?)",
                        ("normalize", "开始"),
                    )
                    raise RuntimeError("模拟失败")
            self.assertEqual("failed", store.get_stage_status("normalize"))
            connection = sqlite3.connect(store.path)
            try:
                count = connection.execute(
                    "SELECT COUNT(*) FROM run_event WHERE stage='normalize'"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(0, count)

    def test_schema_has_exactly_the_seventeen_approved_tables(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RunStore(Path(tmp) / "trace.sqlite3")
            store.initialize()
            connection = sqlite3.connect(store.path)
            try:
                actual = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                    )
                }
            finally:
                connection.close()
            self.assertEqual(EXPECTED_TABLES, actual)


if __name__ == "__main__":
    unittest.main()
