from __future__ import annotations

import tempfile
import time
import unittest
import hashlib
from pathlib import Path

from cashflow_direct.pipeline import confirm_cash_scope, finalize_run, run_classification, run_preflight
from tests.fixture_factory import write_large_case


class LargeCaseTests(unittest.TestCase):
    def test_100k_rows_are_fully_scanned_and_reconciled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, expected_cash_delta = write_large_case(root, row_count=100_000)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            source_size = source.stat().st_size
            started = time.perf_counter()
            preflight = run_preflight(
                [source],
                ("100000000", "75000000", "5000000"),
                output_parent=root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            classified = run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)
            elapsed = time.perf_counter() - started
            self.assertEqual(100_000, classified.source_entry_count)
            self.assertEqual(expected_cash_delta, classified.cash_delta_cent)
            self.assertTrue(final.workbook_path.is_file())
            self.assertEqual("final_usable", final.overall_status)
            self.assertLess(elapsed, 300)
            self.assertEqual(source_hash, hashlib.sha256(source.read_bytes()).hexdigest())
            print(
                f"PERF_100K elapsed_seconds={elapsed:.3f} "
                f"input_bytes={source_size} output_bytes={final.workbook_path.stat().st_size}"
            )


if __name__ == "__main__":
    unittest.main()
