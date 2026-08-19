from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook
from openpyxl import load_workbook

from cashflow_direct.pipeline import (
    confirm_company_notes,
    confirm_mapping,
    confirm_cash_scope,
    finalize_run,
    import_ai_results,
    import_dictionary_results,
    run_classification,
    run_preflight,
    scan_accounts,
    supplement_cash_balances,
)
from tests.fixture_factory import (
    mark_dictionary_complete,
    write_ai_batch_case,
    write_ai_end_to_end_case,
    write_ambiguous_money_fixture,
    write_detail_plus_statement_fixture,
    write_end_to_end_case,
    write_existing_statement_fixture,
)


class PipelineTests(unittest.TestCase):
    def test_original_and_auto_item_difference_is_exported_without_affecting_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "原项目与自动判定不一致.xlsx"
            workbook = Workbook()
            detail = workbook.active
            detail.title = "序时账"
            detail.append(
                [
                    "日期",
                    "凭证字",
                    "凭证号",
                    "摘要",
                    "科目编码",
                    "科目名称",
                    "借方",
                    "贷方",
                    "主表项目名称",
                ]
            )
            detail.append(
                ["2026/1/1", "记", "1", "支付税收滞纳金", "1002", "银行存款", None, 100, None]
            )
            detail.append(
                [
                    "2026/1/1",
                    "记",
                    "1",
                    "支付税收滞纳金",
                    "6711",
                    "营业外支出",
                    100,
                    None,
                    "客户自定义项目",
                ]
            )
            balances = workbook.create_sheet("现金余额资料")
            balances.append(["项目", "金额"])
            balances.append(["期初现金及现金等价物余额", 1_000])
            balances.append(["汇率变动影响", 0])
            balances.append(["期末现金及现金等价物余额", 900])
            workbook.save(source)
            workbook.close()

            preflight = run_preflight(
                [source], ("1000000", "750000", "50000"), output_parent=root
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            supplement_cash_balances(
                preflight.run_dir, "1000", "900", "0", "测试现金余额"
            )
            # 本用例不涉及词典机制，直接标记齐备（门禁通行）
            mark_dictionary_complete(preflight.run_dir)
            run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)

            self.assertEqual("最终可使用", final.overall_status)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            self.assertEqual(0, state["reconciliation"]["difference_cent"])
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                sheet = workbook["原表与自动判定差异"]
                headers = [cell.value for cell in sheet[1]]
                row = {
                    header: sheet.cell(2, column).value
                    for column, header in enumerate(headers, start=1)
                }
                self.assertEqual(2, sheet.max_row)
                self.assertEqual("记", row["凭证字"])
                self.assertEqual("1", str(row["凭证号"]))
                self.assertEqual("6711", str(row["科目编码"]))
                self.assertEqual("客户自定义项目", row["主表项目名称"])
                self.assertEqual("支付其他与经营活动有关的现金", row["自动判定现流项目"])
                self.assertEqual(100, row["借方"])
                self.assertIsNone(row["流量金额（原币）"])
                # 重构后口径：本组成自动判定(CFO-07 营业外支出/滞纳金)→支付其他经营；原标签客户自定义无法标准化
                self.assertEqual("原项目无法标准化", row["差异说明"])
            finally:
                workbook.close()

    def test_unresolved_consistency_group_does_not_duplicate_human_review_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "未收口业务组.xlsx"
            workbook = Workbook()
            detail = workbook.active
            detail.title = "明细"
            detail.append(
                ["日期", "凭证字", "凭证号", "分录号", "摘要", "科目编码", "科目", "借方", "贷方", "流量金额", "现流项目"]
            )
            detail.append(
                ["2026/6/15", "记", "70", "2", "支付款项", "2001.03", "短期借款_财务", None, 60_000, 60_000, None]
            )
            detail.append(
                ["2026/6/15", "记", "70", "3", "支付款项", "2211.03", "应付职工薪酬_财务", None, 60_000, 60_000, None]
            )
            balances = workbook.create_sheet("现金余额资料")
            balances.append(["项目", "金额"])
            balances.append(["期初现金及现金等价物余额", 0])
            balances.append(["汇率变动影响", 0])
            balances.append(["期末现金及现金等价物余额", 120_000])
            workbook.save(source)
            workbook.close()

            preflight = run_preflight([source], ("100000", "50000", "5000"), root)
            confirm_cash_scope(preflight.run_dir, {})
            supplement_cash_balances(preflight.run_dir, "0", "120000", "0", "测试现金余额")
            # 本用例不涉及词典机制，直接标记齐备（门禁通行）
            mark_dictionary_complete(preflight.run_dir)
            run_classification(preflight.run_dir)
            state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            first_path = root / "逐条结果.jsonl"
            first_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "task_id": task["task_id"],
                            "component_id": task["component_id"],
                            "item_id": task["system_item_id"],
                            "reason": "知识库第1行：保留逐条判断",
                            "confidence": "high",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                    for task in state["ai_tasks"]
                ),
                encoding="utf-8-sig",
            )
            import_ai_results(preflight.run_dir, first_path)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            task = state["consistency_tasks"][0]
            group_first_path = root / "一致性首次结果.jsonl"
            group_first_path.write_text(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "group_id": task["group_id"],
                        "assignments": dict(task["current_assignments"]),
                        "reason": "知识库第2行：首次复核仍不足以收口",
                        "confidence": "low",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )
            import_ai_results(preflight.run_dir, group_first_path)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            task = state["consistency_adjudication_tasks"][0]
            group_second_path = root / "一致性裁决结果.jsonl"
            group_second_path.write_text(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "group_id": task["group_id"],
                        "assignments": dict(task["current_assignments"]),
                        "reason": "知识库第3行：第二轮仍不足以收口",
                        "confidence": "low",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )
            import_ai_results(preflight.run_dir, group_second_path)
            final = finalize_run(preflight.run_dir)
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                review = workbook["重要待复核事项"]
                self.assertEqual(3, review.max_row)
            finally:
                workbook.close()

    def test_material_voucher_split_gets_two_group_reviews_before_unifying_items(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "同一业务组.xlsx"
            workbook = Workbook()
            detail = workbook.active
            detail.title = "明细"
            detail.append(
                ["日期", "凭证字", "凭证号", "分录号", "摘要", "科目编码", "科目", "借方", "贷方", "流量金额", "现流项目"]
            )
            detail.append(
                ["2026/6/15", "记", "70", "2", "支付款项", "2001.03", "短期借款_财务", None, 60_000, 60_000, None]
            )
            detail.append(
                ["2026/6/15", "记", "70", "3", "支付款项", "2211.03", "应付职工薪酬_财务", None, 60_000, 60_000, None]
            )
            balances = workbook.create_sheet("现金余额资料")
            balances.append(["项目", "金额"])
            balances.append(["期初现金及现金等价物余额", 0])
            balances.append(["汇率变动影响", 0])
            balances.append(["期末现金及现金等价物余额", 120_000])
            workbook.save(source)
            workbook.close()

            preflight = run_preflight(
                [source], ("100000", "50000", "5000"), root
            )
            confirm_cash_scope(preflight.run_dir, {})
            supplement_cash_balances(
                preflight.run_dir, "0", "120000", "0", "测试现金余额"
            )
            # 本用例不涉及词典机制，直接标记齐备（门禁通行）
            mark_dictionary_complete(preflight.run_dir)
            classified = run_classification(preflight.run_dir)
            self.assertEqual(2, classified.ai_tasks_missing)
            state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))

            first_path = root / "逐条首次结果.jsonl"
            first_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "task_id": task["task_id"],
                            "component_id": task["component_id"],
                            "item_id": task["system_item_id"],
                            "reason": "知识库第4行：先保留逐条系统判断",
                            "confidence": "high",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                    for task in state["ai_tasks"]
                ),
                encoding="utf-8-sig",
            )
            first = import_ai_results(preflight.run_dir, first_path)
            self.assertEqual("AI 待一致性复核", first.status)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual("waiting_consistency", state["stage"])
            self.assertEqual(1, len(state["consistency_tasks"]))

            task = state["consistency_tasks"][0]
            group_first_path = root / "一致性首次结果.jsonl"
            group_first_path.write_text(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "group_id": task["group_id"],
                        "assignments": {
                            component_id: "CFI-06"
                            for component_id in task["component_ids"]
                        },
                        "reason": "知识库第5行：整组属于同一项长期资产退款",
                        "confidence": "high",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )
            group_first = import_ai_results(preflight.run_dir, group_first_path)
            self.assertEqual("AI 待一致性裁决", group_first.status)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual("waiting_consistency_adjudication", state["stage"])

            task = state["consistency_adjudication_tasks"][0]
            group_second_path = root / "一致性裁决结果.jsonl"
            group_second_path.write_text(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "group_id": task["group_id"],
                        "assignments": {
                            component_id: "CFI-06"
                            for component_id in task["component_ids"]
                        },
                        "reason": "知识库第6行：独立整组裁决确认属于长期资产退款",
                        "confidence": "high",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )
            group_second = import_ai_results(preflight.run_dir, group_second_path)
            self.assertEqual("AI 已完成", group_second.status)

            final = finalize_run(preflight.run_dir)
            self.assertEqual("最终可使用", final.overall_status)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual(
                {"CFI-06"},
                {item["system_item_id"] for item in state["decisions"]},
            )
            self.assertEqual(0, state["reconciliation"]["difference_cent"])
            workbook = load_workbook(final.workbook_path, data_only=True)
            try:
                sheet = workbook["现金流量表正表"]
                values = {
                    sheet.cell(row, 2).value: sheet.cell(row, 6).value
                    for row in range(1, sheet.max_row + 1)
                }
                self.assertEqual(-120_000, values["购建固定资产、无形资产和其他长期资产支付的现金"])
                trace = workbook["全量分类留痕"]
                headers = [cell.value for cell in trace[1]]
                for header in (
                    "一致性复核状态",
                    "一致性复核理由",
                    "一致性重要性层级",
                    "业务组编号(技术)",
                ):
                    self.assertIn(header, headers)
                status_column = headers.index("一致性复核状态") + 1
                self.assertEqual(
                    {"重大一致性复核已收口"},
                    {
                        trace.cell(row, status_column).value
                        for row in range(2, trace.max_row + 1)
                    },
                )
                group_column = headers.index("业务组编号(技术)") + 1
                self.assertTrue(
                    any(
                        dimension.hidden
                        and dimension.min <= group_column <= dimension.max
                        for dimension in trace.column_dimensions.values()
                    )
                )
                ai_sheet = workbook["AI复核记录"]
                ai_headers = [cell.value for cell in ai_sheet[1]]
                stage_column = ai_headers.index("阶段") + 1
                self.assertEqual(
                    {"首次复核", "一致性复核", "一致性裁决"},
                    {
                        ai_sheet.cell(row, stage_column).value
                        for row in range(2, ai_sheet.max_row + 1)
                    },
                )
            finally:
                workbook.close()

    def test_statement_path_is_honored_and_failure_stops(self) -> None:
        # 指定的文件识别不出正表 → 报错停下（不静默降级为编制模式）
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (journal,) = write_end_to_end_case(root)
            bogus = root / "指定但不是正表.xlsx"
            workbook = Workbook()
            workbook.active.append(["不是", "正表", "内容"])
            workbook.save(bogus)
            with self.assertRaises(ValueError):
                run_preflight(
                    [journal, bogus], ("1000000", "750000", "50000"), statement_path=bogus
                )

    def test_statement_path_success_skips_auto_detection_for_others(self) -> None:
        # 指定合法正表：该文件走正表识别并登记，其余文件只按明细处理
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            journal, existing = write_end_to_end_case(root, include_existing_statement=True)
            preflight = run_preflight(
                [journal, existing],
                ("1000000", "750000", "50000"),
                output_parent=root,
                statement_path=existing,
            )
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            self.assertEqual(str(existing), state["existing_statement_path"])
            self.assertIn(str(state["files"][0]["file_id"]), state["evidence_profiles"])
            profile = state["evidence_profiles"][str(state["files"][0]["file_id"])]
            self.assertTrue(profile["has_flow_item"])
            self.assertTrue(profile["full_voucher"])
            self.assertEqual(340_000, state["cash_balances"]["opening_cent"])
            self.assertEqual(350_000, state["cash_balances"]["closing_cent"])
            self.assertEqual(320_000, state["cash_balances"]["fx_cent"])

    def test_statement_path_outside_inputs_raises(self) -> None:
        # 指定的正表文件不在已选输入中 → 必须报错，不得静默跳过
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (journal,) = write_end_to_end_case(root)
            missing = root / "未选中的文件.xlsx"
            with self.assertRaises(ValueError):
                run_preflight(
                    [journal], ("1000000", "750000", "50000"), statement_path=missing
                )

    def test_trace_marks_direction_source(self) -> None:
        # 全量分类留痕表含"方向依据"列，序时账明细（无流量金额列）标"借贷差额"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inputs = write_end_to_end_case(root)
            preflight = run_preflight(
                inputs, ("1000000", "750000", "50000"), output_parent=root
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                sheet = workbook["全量分类留痕"]
                headers = [cell.value for cell in sheet[1]]
                self.assertIn("方向依据", headers)
                column = headers.index("方向依据") + 1
                values = {
                    sheet.cell(row, column).value for row in range(2, sheet.max_row + 1)
                }
                self.assertIn("借贷差额", values)
            finally:
                workbook.close()

    def test_multiple_statement_sheet_names_survive_preflight_for_user_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "多张客户正表.xlsx"
            write_existing_statement_fixture(source, header_row=7, with_custom_rows=False)
            workbook = load_workbook(source)
            duplicate = workbook.copy_worksheet(workbook.worksheets[0])
            duplicate.title = "另一张现金流量表"
            workbook.save(source)
            workbook.close()

            preflight = run_preflight(
                [source], ("1000000", "750000", "50000"), output_parent=root
            )
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            question = state["mapping_questions"][0]
            self.assertEqual("statement_sheet", question["role"])
            self.assertIn("报表页_随机", question["message"])
            self.assertIn("另一张现金流量表", question["message"])
            with self.assertRaisesRegex(RuntimeError, "报表页_随机.*另一张现金流量表"):
                confirm_mapping(preflight.run_dir, {})

    def test_ai_result_batches_accumulate_without_resubmitting_completed_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                [write_ai_batch_case(root)],
                ("1000000", "750000", "50000"),
                root,
            )
            confirm_cash_scope(preflight.run_dir, {})
            classified = run_classification(preflight.run_dir)
            self.assertEqual(26, classified.ai_tasks_missing)
            state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
            tasks = json.loads(state_path.read_text(encoding="utf-8-sig"))["ai_tasks"]

            def write_results(path: Path, selected: list[dict[str, object]]) -> None:
                path.write_text(
                    "".join(
                        json.dumps(
                            {
                                "task_id": item["task_id"],
                                "component_id": item["component_id"],
                                "item_id": "CFO-03",
                                "reason": "知识库第7行：原标签与摘要一致",
                                "confidence": "high",
                            },
                            ensure_ascii=False,
                        ) + "\n"
                        for item in selected
                    ),
                    encoding="utf-8-sig",
                )

            first_path = root / "第一批结果.jsonl"
            write_results(first_path, tasks[:25])
            first = import_ai_results(preflight.run_dir, first_path)
            self.assertEqual(25, first.valid_count)
            self.assertEqual(1, first.missing_count)

            second_path = root / "第二批结果.jsonl"
            write_results(second_path, tasks[25:])
            second = import_ai_results(preflight.run_dir, second_path)
            self.assertEqual(26, second.valid_count)
            self.assertEqual(0, second.missing_count)
            self.assertEqual("AI 已完成", second.status)

            conflicting_path = root / "冲突结果.jsonl"
            conflicting_path.write_text(
                json.dumps(
                    {
                        "task_id": tasks[0]["task_id"],
                        "component_id": tasks[0]["component_id"],
                        "item_id": "CFI-05",
                        "reason": "知识库第8行：与已导入结果冲突",
                        "confidence": "high",
                    },
                    ensure_ascii=False,
                ) + "\n",
                encoding="utf-8-sig",
            )
            with self.assertRaisesRegex(ValueError, "冲突"):
                import_ai_results(preflight.run_dir, conflicting_path)

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
            with self.assertRaisesRegex(RuntimeError, "补充期初、期末现金余额和汇率影响"):
                run_classification(preflight.run_dir)
            supplement_cash_balances(preflight.run_dir, "0", "100", "0", "匿名余额资料")
            run_classification(preflight.run_dir)
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
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            statement_question = next(
                question
                for question in state["mapping_questions"]
                if question.get("kind") == "statement"
            )
            confirm_mapping(
                preflight.run_dir,
                {
                    f"{statement_question['file_id']}:statement:{statement_question['sheet']}": "use"
                },
            )
            supplement_cash_balances(preflight.run_dir, "1000", "1060", "0", "匿名余额资料")
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

    def test_missing_balances_block_classify_until_supplemented(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                write_end_to_end_case(root, include_cash_balances=False),
                ("1000000", "750000", "50000"),
                root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            with self.assertRaisesRegex(RuntimeError, "补充期初、期末现金余额和汇率影响"):
                run_classification(preflight.run_dir)
            supplement_cash_balances(preflight.run_dir, "1000", "1060", "0", "客户盖章现金余额表")
            run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)
            self.assertEqual("最终可使用", final.overall_status)

    def test_missing_balances_can_be_supplemented_without_new_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                write_end_to_end_case(root, include_cash_balances=False),
                ("1000000", "750000", "50000"),
                root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            with self.assertRaisesRegex(RuntimeError, "补充期初、期末现金余额和汇率影响"):
                run_classification(preflight.run_dir)
            supplement_cash_balances(
                preflight.run_dir, "1000", "1060", "0", "客户盖章现金余额表"
            )
            run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)
            self.assertEqual("最终可使用", final.overall_status)

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

    def test_same_file_detail_and_statement_prompts_and_reconciles(self) -> None:
        # 同一文件含明细+正表：不带 statement-path → 明细不丢，且产生疑似正表提问；确认 use 后生成核对报告
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "明细加正表.xlsx"
            write_detail_plus_statement_fixture(source)
            preflight = run_preflight(
                [source], ("1000000", "750000", "50000"), output_parent=root
            )
            self.assertGreater(preflight.source_entry_count, 0)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            statement_questions = [
                question
                for question in state["mapping_questions"]
                if question.get("kind") == "statement"
            ]
            self.assertEqual(1, len(statement_questions))
            key = (
                f"{statement_questions[0]['file_id']}:statement:"
                f"{statement_questions[0]['sheet']}"
            )
            confirm_mapping(preflight.run_dir, {key: "use"})
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            self.assertIn("opening_cent", state["cash_balances"])
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                self.assertIsNotNone(workbook["正表核对报告"]["A2"].value)
            finally:
                workbook.close()

    def test_same_file_with_statement_path_keeps_detail(self) -> None:
        # 同一文件带 --statement-path 指向自身 → 明细仍被读取，且正表路径已登记
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "明细加正表.xlsx"
            write_detail_plus_statement_fixture(source)
            preflight = run_preflight(
                [source],
                ("1000000", "750000", "50000"),
                output_parent=root,
                statement_path=source,
            )
            self.assertGreater(preflight.source_entry_count, 0)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            self.assertEqual(str(source), state["existing_statement_path"])

    def test_unconfirmed_statement_blocks_final_usable(self) -> None:
        # 疑似正表未纳入核对（确认 ignore）→ 不允许"最终可使用"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "明细加正表.xlsx"
            write_detail_plus_statement_fixture(source)
            preflight = run_preflight(
                [source], ("1000000", "750000", "50000"), output_parent=root
            )
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            statement_question = next(
                question
                for question in state["mapping_questions"]
                if question.get("kind") == "statement"
            )
            key = (
                f"{statement_question['file_id']}:statement:"
                f"{statement_question['sheet']}"
            )
            confirm_mapping(preflight.run_dir, {key: "ignore"})
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            supplement_cash_balances(preflight.run_dir, "0", "60", "0", "匿名余额资料")
            run_classification(preflight.run_dir)
            final = finalize_run(preflight.run_dir)
            self.assertNotEqual("最终可使用", final.overall_status)

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
            initial_decision = state["decisions"][0]
            self.assertEqual("CFO-03", initial_decision["system_item_id"])
            self.assertIn("摘要包含", initial_decision["reason"])
            self.assertNotIn("CFO-03-CURRENT", initial_decision["reason"])
            self.assertNotIn("完全一致", initial_decision["reason"])
            task = state["ai_tasks"][0]
            first_result = root / "AI首次结果.jsonl"
            first_result.write_text(
                json.dumps(
                    {
                        "task_id": task["task_id"],
                        "component_id": task["component_id"],
                        "item_id": "CFI-05",
                        "reason": "知识库第9行：摘要表明属于其他投资活动",
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
                        "reason": "知识库第10行：裁决确认投资证据清楚",
                        "confidence": "high",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8-sig",
            )
            second = import_ai_results(preflight.run_dir, second_result)
            self.assertEqual("AI 已完成", second.status)
            repeated = import_ai_results(preflight.run_dir, second_result)
            self.assertEqual("AI 已完成", repeated.status)
            self.assertEqual(0, repeated.missing_count)
            final = finalize_run(preflight.run_dir)
            self.assertEqual("最终可使用", final.overall_status)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual("CFI-05", state["decisions"][0]["system_item_id"])
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                ai_sheet = workbook["AI复核记录"]
                self.assertEqual(3, ai_sheet.max_row)
                self.assertIn("阶段", [cell.value for cell in ai_sheet[1]])
                trace_headers = [cell.value for cell in workbook["全量分类留痕"][1]]
                for header in (
                    "原现流项目",
                    "对方科目",
                    "自动判定现流项目",
                    "证据强度",
                    "来源文件",
                    "来源工作表",
                    "来源单元格",
                ):
                    self.assertIn(header, trace_headers)
            finally:
                workbook.close()
            connection = sqlite3.connect(preflight.run_dir / "计算留痕数据" / "计算留痕.sqlite3")
            try:
                self.assertEqual(2, connection.execute("SELECT COUNT(*) FROM ai_result").fetchone()[0])
                final_payload = json.loads(
                    connection.execute(
                        "SELECT payload_json FROM classification_decision WHERE record_id = ?",
                        (state["decisions"][0]["component_id"],),
                    ).fetchone()[0]
                )
                self.assertEqual("CFI-05", final_payload["system_item_id"])
            finally:
                connection.close()

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
                        "reason": "知识库第11行：可能属于投资活动",
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
                        "reason": "知识库第12行：证据仍然不足",
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
                self.assertEqual(
                    "收到其他与经营活动有关的现金",
                    workbook["重要待复核事项"]["B2"].value,
                )
                self.assertIn(
                    "匿名弱证据明细.xlsx",
                    workbook["重要待复核事项"]["O2"].value,
                )
            finally:
                workbook.close()

    def test_classify_requires_balances_before_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            preflight = run_preflight(
                write_end_to_end_case(root, include_cash_balances=False),
                ("1000000", "750000", "50000"),
                root,
            )
            confirm_cash_scope(preflight.run_dir, preflight.recommended_cash_decisions)
            with self.assertRaisesRegex(RuntimeError, "补充期初、期末现金余额和汇率影响"):
                run_classification(preflight.run_dir)
            supplement_cash_balances(preflight.run_dir, "1000", "1060", "0", "客户盖章现金余额表")
            classified = run_classification(preflight.run_dir)
            self.assertEqual(0, classified.ai_tasks_missing)

    def test_rough_reconciliation_runs_before_and_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "单边明细.xlsx"
            workbook = Workbook()
            detail = workbook.active
            detail.title = "单边数据"
            detail.append(["日期", "凭证号", "摘要", "科目", "借方", "贷方", "流量金额", "现流项目"])
            detail.append(["2026-01-01", "记-1", "匿名付款", "应付账款", 100, None, 100, "购买商品、接受劳务支付的现金"])
            balance = workbook.create_sheet("现金余额资料")
            balance.append(["项目", "金额"])
            balance.append(["期初现金及现金等价物余额", 200])
            balance.append(["期末现金及现金等价物余额", 100])
            balance.append(["汇率变动对现金及现金等价物的影响", 0])
            workbook.save(source)
            preflight = run_preflight([source], ("1000000", "750000", "50000"), root)
            confirm_cash_scope(preflight.run_dir, {})
            run_classification(preflight.run_dir)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            rough = state["rough_reconciliation"]
            self.assertTrue(rough["applicable"])
            self.assertEqual("相符", rough["status"])
            self.assertEqual(-10_000, rough["detail_sum_cent"])
            trace = preflight.run_dir / "计算留痕数据" / "粗勾稽留痕.jsonl"
            self.assertTrue(trace.is_file())
            self.assertIn("detail_sum_cent", trace.read_text(encoding="utf-8-sig"))

    def test_classify_blocked_until_dictionary_completed(self) -> None:
        # 含未知明细对方科目时，classify 必须先停在"待科目语义确认"；导入后放行（Task 5）
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "含明细科目.xlsx"
            workbook = Workbook()
            journal = workbook.active
            journal.title = "序时账"
            journal.append(["日期", "凭证字", "凭证号", "摘要", "科目编码", "科目", "借方", "贷方", "流量金额", "现流项目"])
            journal.append(["2026/1/1", "记", "1", "支付设备款", "1002", "银行存款", None, 100000, None, "购建固定资产支付的现金"])
            journal.append(["2026/1/1", "记", "1", "支付设备款", "2202", "应付账款_全新明细段XYZ", 100000, None, None, "购建固定资产支付的现金"])
            balances = workbook.create_sheet("现金余额资料")
            balances.append(["项目", "金额"])
            balances.append(["期初现金及现金等价物余额", 0])
            balances.append(["汇率变动对现金及现金等价物的影响", 0])
            balances.append(["期末现金及现金等价物余额", 100000])
            workbook.save(source)
            workbook.close()

            preflight = run_preflight([source], ("100000", "50000", "5000"), root)
            confirm_cash_scope(preflight.run_dir, dict(preflight.recommended_cash_decisions))
            result = run_classification(preflight.run_dir)
            self.assertEqual("待科目语义确认", result.status)
            self.assertGreater(result.ai_tasks_missing, 0)

            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            tasks = state["account_dictionary"]["tasks"]
            batch_files = list((preflight.run_dir / "计算留痕数据").glob("科目语义待判断_*.jsonl"))
            self.assertTrue(batch_files)
            result_path = root / "词典结果.jsonl"
            result_path.write_text(
                "".join(
                    json.dumps(
                        {
                            "task_id": task["task_id"],
                            "account": task["account"],
                            "semantic": "测试语义",
                            "item_id": "CFI-06",
                            "confidence": "high",
                            "basis": "知识库第13行：测试依据",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                    for task in tasks
                ),
                encoding="utf-8-sig",
            )
            import_result = import_dictionary_results(preflight.run_dir, result_path)
            self.assertEqual("科目语义已导入", import_result["status"])
            second = run_classification(preflight.run_dir)
            self.assertNotEqual("待科目语义确认", second.status)
            self.assertEqual("科目语义词典说明.md", (preflight.run_dir / "科目语义词典说明.md").name)
            self.assertTrue((preflight.run_dir / "科目语义词典说明.md").is_file())

    def test_company_notes_gate_and_injection(self) -> None:
        # 传入 --notes 后，未 confirm-notes 前 scan-accounts 停在待确认；确认后注意事项进入词典批次 context（Task 5B）
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "含明细科目.xlsx"
            workbook = Workbook()
            journal = workbook.active
            journal.title = "序时账"
            journal.append(["日期", "凭证字", "凭证号", "摘要", "科目编码", "科目", "借方", "贷方", "流量金额", "现流项目"])
            journal.append(["2026/1/1", "记", "1", "支付设备款", "1002", "银行存款", None, 100000, None, "购建固定资产支付的现金"])
            journal.append(["2026/1/1", "记", "1", "支付设备款", "2202", "应付账款_全新明细段XYZ", 100000, None, None, "购建固定资产支付的现金"])
            balances = workbook.create_sheet("现金余额资料")
            balances.append(["项目", "金额"])
            balances.append(["期初现金及现金等价物余额", 0])
            balances.append(["汇率变动对现金及现金等价物的影响", 0])
            balances.append(["期末现金及现金等价物余额", 100000])
            workbook.save(source)
            workbook.close()

            notes_text = "该公司把应付设备款做到其他应付款"
            preflight = run_preflight(
                [source], ("100000", "50000", "5000"), root, notes=notes_text
            )
            confirm_cash_scope(preflight.run_dir, dict(preflight.recommended_cash_decisions))
            blocked = scan_accounts(preflight.run_dir)
            self.assertEqual("待确认公司特殊规则", blocked["status"])

            confirmed = confirm_company_notes(
                preflight.run_dir,
                [
                    {
                        "note_id": "NOTE-01",
                        "内容": "该公司把应付设备款做到其他应付款",
                        "涉及科目或词": ["其他应付款", "设备款"],
                        "建议处理": "其他应付款中的设备采购支出按购建固定资产判断",
                        "依据": "管理层核算习惯说明",
                    }
                ],
            )
            self.assertEqual("completed", confirmed.status)

            scan = scan_accounts(preflight.run_dir)
            self.assertEqual("待科目语义确认", scan["status"])
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            task = state["account_dictionary"]["tasks"][0]
            self.assertIn("company_notes", task)
            self.assertTrue(task["company_notes"])

    def test_confirm_notes_without_raw_text(self) -> None:
        # 复核修复：无 --notes 文本时也可登记口述规则；缺省状态"采用"、自动生成 NOTE 编号
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_ledger_case(root)
            preflight = run_preflight([source], ("100000", "50000", "5000"), root)

            confirmed = confirm_company_notes(
                preflight.run_dir,
                [{"内容": "电费押金走其他应收", "涉及科目或词": ["押金"]}],
            )

            self.assertEqual("completed", confirmed.status)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            self.assertEqual("NOTE-01", state["company_notes"][0]["note_id"])
            self.assertEqual("采用", state["company_notes"][0]["状态"])

    def test_conflicted_notes_are_never_injected(self) -> None:
        # 复核修复："冲突未采用"规则不得进入科目语义任务上下文与 AI 任务上下文
        from cashflow_direct.ai_review import build_ai_task
        from cashflow_direct.models import CashflowComponent, ClassificationDecision

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_ledger_case(root)
            preflight = run_preflight([source], ("100000", "50000", "5000"), root)
            confirm_company_notes(
                preflight.run_dir,
                [
                    {"note_id": "NOTE-01", "内容": "采用规则甲", "涉及科目或词": ["全新明细段XYZ"]},
                    {"note_id": "NOTE-02", "内容": "冲突规则乙", "涉及科目或词": ["全新明细段XYZ"], "状态": "冲突未采用"},
                ],
            )
            confirm_cash_scope(preflight.run_dir, dict(preflight.recommended_cash_decisions))
            scan = scan_accounts(preflight.run_dir)

            self.assertEqual("待科目语义确认", scan["status"])
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            notes_in_tasks = str(
                [task.get("company_notes") for task in state["account_dictionary"]["tasks"]]
            )
            self.assertIn("采用规则甲", notes_in_tasks)
            self.assertNotIn("冲突规则乙", notes_in_tasks)

            component = CashflowComponent(
                component_id="C1", voucher_key="V1", summary="支付全新明细段XYZ款项",
                cash_delta_cent=-100, counterpart_accounts=("应付账款_全新明细段XYZ",),
            )
            decision = ClassificationDecision(
                component_id="C1", system_item_id="CFI-06",
                system_item_name="购建固定资产、无形资产和其他长期资产支付的现金",
                normal_direction="outflow", matched_rule_id="TEST",
                reason="测试", evidence_level="medium",
            )
            ai_task = build_ai_task(component, decision, state["company_notes"])
            self.assertIn("采用规则甲", ai_task.context)
            self.assertNotIn("冲突规则乙", ai_task.context)

    def test_dictionary_import_rejects_misaligned_account(self) -> None:
        # 复核修复：结果行 account 与任务 account 不一致 → 该行无效并列入 missing（防张冠李戴）
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_ledger_case(root)
            preflight = run_preflight([source], ("100000", "50000", "5000"), root)
            confirm_cash_scope(preflight.run_dir, dict(preflight.recommended_cash_decisions))
            scan_accounts(preflight.run_dir)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            task = state["account_dictionary"]["tasks"][0]
            result_path = root / "词典结果.jsonl"
            result_path.write_text(
                json.dumps({
                    "task_id": task["task_id"],
                    "account": "张冠李戴段",
                    "semantic": "测试语义", "item_id": "CFI-06",
                    "confidence": "high", "basis": "知识库第1行：测试依据",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8-sig",
            )

            result = import_dictionary_results(preflight.run_dir, result_path)

            self.assertEqual("AI 未完成", result["status"])
            self.assertIn(task["task_id"], result["missing_ids"])

    def test_dictionary_import_rejects_unknown_note_id(self) -> None:
        # 复核修复：结果行 note_id 不在已登记"采用"清单 → 无效并列入 missing
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_ledger_case(root)
            preflight = run_preflight([source], ("100000", "50000", "5000"), root)
            confirm_cash_scope(preflight.run_dir, dict(preflight.recommended_cash_decisions))
            scan_accounts(preflight.run_dir)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            task = state["account_dictionary"]["tasks"][0]
            result_path = root / "词典结果.jsonl"
            result_path.write_text(
                json.dumps({
                    "task_id": task["task_id"], "account": task["account"],
                    "semantic": "测试语义", "item_id": "CFI-06",
                    "confidence": "high", "basis": "依据公司特殊规则：NOTE-99",
                    "note_id": "NOTE-99",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8-sig",
            )

            result = import_dictionary_results(preflight.run_dir, result_path)

            self.assertEqual("AI 未完成", result["status"])
            self.assertIn(task["task_id"], result["missing_ids"])

    def test_scan_accounts_forces_task_for_adopted_note_segment(self) -> None:
        # 复核修复：通用词典已知的科目段被"采用"规则涉及词命中时，仍强制生成专属确认任务（防截断）
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_ledger_case(root, account_name="其他应付款_设备款")
            preflight = run_preflight([source], ("100000", "50000", "5000"), root)
            confirm_cash_scope(preflight.run_dir, dict(preflight.recommended_cash_decisions))

            first = scan_accounts(preflight.run_dir)
            # "设备款"通用词典已知，无公司规则时不生成任务
            self.assertEqual("科目语义已齐备", first["status"])

            confirm_company_notes(
                preflight.run_dir,
                [{"note_id": "NOTE-01", "内容": "设备款实际为职工垫付报销", "涉及科目或词": ["设备款"]}],
            )
            second = scan_accounts(preflight.run_dir)

            self.assertEqual("待科目语义确认", second["status"])
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            accounts = [task["account"] for task in state["account_dictionary"]["tasks"]]
            self.assertIn("设备款", accounts)
            # 设计 3.1.3：任务上下文必须注明对应 NOTE 编号，答题方才能在结果里留痕
            task_notes = [
                note
                for task in state["account_dictionary"]["tasks"]
                for note in task.get("company_notes", ())
            ]
            self.assertTrue(any("NOTE-01" in note for note in task_notes))
            # 生成了待确认任务后，主流程必须重新等待词典导入，不得沿用"已齐备"标志
            self.assertFalse(state.get("account_dictionary_completed", False))

    def test_dictionary_note_id_flows_into_classification_reason(self) -> None:
        # 复核修复：导入条目带 NOTE 编号，分类命中后理由须留痕"依据公司特殊规则：NOTE-01"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = _write_ledger_case(root)
            preflight = run_preflight([source], ("100000", "50000", "5000"), root)
            confirm_company_notes(
                preflight.run_dir,
                [{"note_id": "NOTE-01", "内容": "全新明细段XYZ是设备采购款", "涉及科目或词": ["全新明细段XYZ"]}],
            )
            confirm_cash_scope(preflight.run_dir, dict(preflight.recommended_cash_decisions))
            scan_accounts(preflight.run_dir)
            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            task = state["account_dictionary"]["tasks"][0]
            result_path = root / "词典结果.jsonl"
            result_path.write_text(
                json.dumps({
                    "task_id": task["task_id"], "account": task["account"],
                    "semantic": "设备采购对价", "item_id": "CFI-06",
                    "confidence": "high", "basis": "依据公司特殊规则：NOTE-01",
                    "note_id": "NOTE-01",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8-sig",
            )
            imported = import_dictionary_results(preflight.run_dir, result_path)
            self.assertEqual("科目语义已导入", imported["status"])

            run_classification(preflight.run_dir)

            state = json.loads(
                (preflight.run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig")
            )
            reasons = json.dumps(state["decisions"], ensure_ascii=False)
            self.assertIn("依据公司特殊规则：NOTE-01", reasons)


def _write_ledger_case(root: Path, account_name: str = "应付账款_全新明细段XYZ") -> Path:
    """最小运行态夹具：一张序时账（含指定对方科目明细段）+ 现金余额资料。"""
    source = root / "最小夹具.xlsx"
    workbook = Workbook()
    journal = workbook.active
    journal.title = "序时账"
    journal.append(["日期", "凭证字", "凭证号", "摘要", "科目编码", "科目", "借方", "贷方", "流量金额", "现流项目"])
    journal.append(["2026/1/1", "记", "1", "支付设备款", "1002", "银行存款", None, 100000, None, "购建固定资产支付的现金"])
    journal.append(["2026/1/1", "记", "1", "支付设备款", "2202", account_name, 100000, None, None, "购建固定资产支付的现金"])
    balances = workbook.create_sheet("现金余额资料")
    balances.append(["项目", "金额"])
    balances.append(["期初现金及现金等价物余额", 0])
    balances.append(["汇率变动对现金及现金等价物的影响", 0])
    balances.append(["期末现金及现金等价物余额", 100000])
    workbook.save(source)
    workbook.close()
    return source


def test_preflight_accepts_explicit_paths(tmp_path, monkeypatch):
    """--paths 给定中文路径时不再弹窗（A1，Task 11）。"""
    from cashflow_direct.cli import main
    from tests.fixture_factory import write_end_to_end_case

    inputs = write_end_to_end_case(tmp_path)
    monkeypatch.setattr(
        "cashflow_direct.cli.choose_input_files",
        lambda: (_ for _ in ()).throw(AssertionError("不应弹窗")),
    )
    code = main(
        [
            "preflight",
            "--overall", "1000000", "--performance", "500000", "--trivial", "50000",
            "--paths", str(inputs[0]),
            "--output-parent", str(tmp_path / "输出"),
        ]
    )
    assert code == 0


if __name__ == "__main__":
    unittest.main()
