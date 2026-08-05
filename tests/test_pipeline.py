from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl import load_workbook

from cashflow_direct.pipeline import (
    confirm_mapping,
    confirm_cash_scope,
    finalize_run,
    import_ai_results,
    run_classification,
    run_preflight,
    supplement_cash_balances,
)
from tests.fixture_factory import (
    write_ai_end_to_end_case,
    write_ambiguous_money_fixture,
    write_end_to_end_case,
)


class PipelineTests(unittest.TestCase):
    def test_preflight_reads_every_data_sheet_and_keeps_row_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "按月序时账.xlsx"
            workbook = Workbook()
            for index, title in enumerate(("一月", "二月")):
                sheet = workbook.active if index == 0 else workbook.create_sheet()
                sheet.title = title
                sheet.append(["日期", "凭证号", "摘要", "科目", "借方", "贷方"])
                amount = 100 if index == 0 else "坏金额"
                sheet.append([f"2026-0{index + 1}-01", "记-1", "匿名收款", "银行存款", amount, None])
            workbook.save(source)
            preflight = run_preflight(
                [source], ("1000000", "750000", "50000"), output_parent=root
            )
            self.assertEqual(1, preflight.source_entry_count)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            self.assertEqual({"一月", "二月"}, {item["sheet"] for item in state["mappings"]})
            self.assertTrue(any(item["kind"] == "错误" for item in state["normalization_issues"]))
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            run_classification(preflight.run_dir)
            supplement_cash_balances(preflight.run_dir, "0", "100", "0", "匿名余额资料")
            final = finalize_run(preflight.run_dir)
            self.assertEqual("草稿：输入存在未处理错误", final.overall_status)

    def test_cash_balance_sheet_in_ten_thousand_yuan_is_scaled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "万元余额.xlsx"
            workbook = Workbook()
            journal = workbook.active
            journal.append(["日期", "凭证号", "摘要", "科目", "借方", "贷方"])
            journal.append(["2026-01-01", "记-1", "匿名收款", "银行存款", 100, None])
            journal.append(["2026-01-01", "记-1", "匿名收款", "主营业务收入", None, 100])
            balance = workbook.create_sheet("现金余额资料")
            balance.append(["金额单位：万元", None])
            balance.append(["期初现金及现金等价物余额", 1])
            balance.append(["期末现金及现金等价物余额", 1.01])
            balance.append(["汇率变动对现金及现金等价物的影响", 0])
            workbook.save(source)
            preflight = run_preflight(
                [source], ("1000000", "750000", "50000"), output_parent=root
            )
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            self.assertEqual(1_000_000, state["cash_balances"]["opening_cent"])
            self.assertEqual(1_010_000, state["cash_balances"]["closing_cent"])

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
            self.assertEqual("最终可使用", final.overall_status)
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                self.assertIsNotNone(workbook["现金流量表正表"]["C4"].value)
            finally:
                workbook.close()

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

    def test_finalize_skips_partial_file_left_by_prior_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                write_end_to_end_case(root),
                ("1000000", "750000", "50000"),
                output_parent=root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            run_classification(preflight.run_dir)
            partial = preflight.run_dir / "现金流量表正表及复核底稿_生成中.xlsx"
            partial.write_bytes("模拟中断残留".encode("utf-8"))
            final = finalize_run(preflight.run_dir)
            self.assertEqual("现金流量表正表及复核底稿_重建2.xlsx", final.workbook_path.name)
            self.assertEqual("模拟中断残留".encode("utf-8"), partial.read_bytes())

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
            self.assertEqual("草稿：现金调节未完成或存在差异", final.overall_status)
            state = json.loads(
                (final.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            self.assertEqual("现金调节未完成", state["reconciliation"]["status"])

    def test_missing_balances_can_be_supplemented_without_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                write_end_to_end_case(root, include_cash_balances=False),
                ("1000000", "750000", "50000"),
                root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            run_classification(preflight.run_dir)
            first = finalize_run(preflight.run_dir)
            self.assertIn("草稿", first.overall_status)
            supplement_cash_balances(
                preflight.run_dir, "1000", "1060", "0", "客户盖章现金余额表"
            )
            final = finalize_run(preflight.run_dir)
            self.assertEqual("最终可使用", final.overall_status)
            self.assertNotEqual(first.workbook_path, final.workbook_path)

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
            request_path = preflight.run_dir / "计算留痕数据" / "AI复核请求_第01批.jsonl"
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
            adjudication_path = preflight.run_dir / "计算留痕数据" / "AI裁决请求_第01批.jsonl"
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
            self.assertEqual("最终可使用", final.overall_status)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual("CFI-05", state["decisions"][0]["system_item_id"])

    def test_low_confidence_ai_conflict_reaches_material_human_review_sheet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                [write_ai_end_to_end_case(root)],
                ("1000000", "750000", "50000"),
                root,
            )
            confirm_cash_scope(preflight.run_dir, {})
            run_classification(preflight.run_dir)
            state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
            task = json.loads(state_path.read_text(encoding="utf-8-sig"))["ai_tasks"][0]
            first_result = root / "首次结果.jsonl"
            first_result.write_text(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "component_id": task["component_id"],
                        "item_id": "CFI-05",
                        "reason": "可能属于投资活动",
                        "confidence": "low",
                    },
                    ensure_ascii=False,
                ) + "\n",
                encoding="utf-8-sig",
            )
            import_ai_results(preflight.run_dir, first_result)
            adjudication = json.loads(state_path.read_text(encoding="utf-8-sig"))["adjudication_tasks"][0]
            second_result = root / "裁决结果.jsonl"
            second_result.write_text(
                json.dumps(
                    {
                        "task_id": adjudication["task_id"],
                        "component_id": adjudication["component_id"],
                        "item_id": "CFI-05",
                        "reason": "证据仍然不足",
                        "confidence": "low",
                    },
                    ensure_ascii=False,
                ) + "\n",
                encoding="utf-8-sig",
            )
            import_ai_results(preflight.run_dir, second_result)
            final = finalize_run(preflight.run_dir)
            self.assertEqual("待完成人工确认", final.overall_status)
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                self.assertIsNone(workbook["重要待复核事项"]["C2"].value)
                self.assertEqual("CFO-03", workbook["重要待复核事项"]["B2"].value)
            finally:
                workbook.close()


if __name__ == "__main__":
    unittest.main()
