from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cashflow_direct.pipeline import (
    confirm_mapping,
    confirm_cash_scope,
    finalize_run,
    import_ai_results,
    run_classification,
    run_preflight,
)
from tests.fixture_factory import (
    write_ai_end_to_end_case,
    write_ambiguous_money_fixture,
    write_end_to_end_case,
)


class PipelineTests(unittest.TestCase):
    def test_compile_and_verify_flow_produces_workbook_and_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_end_to_end_case(root, include_existing_statement=True)
            preflight = run_preflight(inputs, ("1000000", "750000", "50000"), output_parent=root)
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            classified = run_classification(preflight.run_dir)
            self.assertEqual(0, classified.ai_tasks_missing)
            final = finalize_run(preflight.run_dir)
            self.assertTrue(final.workbook_path.is_file())
            self.assertTrue((final.run_dir / "计算留痕数据" / "计算留痕.sqlite3").is_file())
            self.assertEqual("final_usable", final.overall_status)

    def test_resume_does_not_duplicate_completed_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                write_end_to_end_case(root),
                ("1000000", "750000", "50000"),
                output_parent=root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            first = run_classification(preflight.run_dir)
            second = run_classification(preflight.run_dir)
            self.assertEqual(first.component_count, second.component_count)
            self.assertEqual(first.component_hash, second.component_hash)

    def test_cash_confirmation_and_input_hash_are_hard_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_end_to_end_case(root)
            preflight = run_preflight(inputs, ("1000000", "750000", "50000"), root)
            with self.assertRaisesRegex(RuntimeError, "确认现金范围"):
                run_classification(preflight.run_dir)
            inputs[0].write_bytes(inputs[0].read_bytes() + b"changed")
            with self.assertRaisesRegex(RuntimeError, "输入文件已被修改.*新运行目录"):
                confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)

    def test_missing_cash_reconciliation_can_only_be_draft(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                write_end_to_end_case(root, include_cash_balances=False),
                ("1000000", "750000", "50000"),
                root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)
            self.assertEqual("draft_cash_reconciliation_incomplete", final.overall_status)
            state = json.loads(
                (final.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            self.assertEqual("现金调节未完成", state["reconciliation"]["status"])

    def test_mapping_confirmation_is_applied_without_restarting_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "歧义明细.xlsx"
            write_ambiguous_money_fixture(source)
            preflight = run_preflight([source], ("1000000", "750000", "50000"), root)
            self.assertEqual(1, preflight.mapping_question_count)
            state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            question = state["mapping_questions"][0]
            with self.assertRaisesRegex(RuntimeError, "字段映射"):
                confirm_cash_scope(preflight.run_dir, {})
            confirm_mapping(
                preflight.run_dir,
                {f"{question['file_id']}:{question['role']}": question["recommended"]},
            )
            updated = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual([], updated["mapping_questions"])
            self.assertEqual(1, len(updated["entries"]))

    def test_ai_conflict_and_adjudication_change_final_statement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = write_ai_end_to_end_case(root)
            preflight = run_preflight([source], ("1000000", "750000", "50000"), root)
            confirm_cash_scope(preflight.run_dir, {})
            classified = run_classification(preflight.run_dir)
            self.assertEqual(1, classified.ai_tasks_missing)
            request_path = preflight.run_dir / "计算留痕数据" / "AI复核请求.jsonl"
            self.assertTrue(request_path.is_file())
            self.assertIn("task_id", request_path.read_text(encoding="utf-8-sig"))
            state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            task = state["ai_tasks"][0]
            first_result = root / "AI首次结果.jsonl"
            first_result.write_text(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "component_id": task["component_id"],
                        "item_id": "CFI-05",
                        "reason": "摘要表明属于其他投资活动",
                        "confidence": "high",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )
            first = import_ai_results(preflight.run_dir, first_result)
            self.assertEqual("AI 待裁决", first.status)
            adjudication_path = preflight.run_dir / "计算留痕数据" / "AI裁决请求.jsonl"
            self.assertTrue(adjudication_path.is_file())
            self.assertIn("task_id", adjudication_path.read_text(encoding="utf-8-sig"))
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            adjudication = state["adjudication_tasks"][0]
            second_result = root / "AI裁决结果.jsonl"
            second_result.write_text(
                json.dumps(
                    {
                        "task_id": adjudication["task_id"],
                        "component_id": adjudication["component_id"],
                        "item_id": "CFI-05",
                        "reason": "裁决确认投资证据清楚",
                        "confidence": "high",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )
            second = import_ai_results(preflight.run_dir, second_result)
            self.assertEqual("AI 已完成", second.status)
            final = finalize_run(preflight.run_dir)
            self.assertEqual("final_usable", final.overall_status)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual("CFI-05", state["decisions"][0]["system_item_id"])


if __name__ == "__main__":
    unittest.main()
