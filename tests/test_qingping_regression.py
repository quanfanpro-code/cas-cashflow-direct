from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from cashflow_direct.classification import load_rule_pack
from cashflow_direct.pipeline import (
    confirm_cash_scope,
    finalize_run,
    import_ai_results,
    run_classification,
    run_preflight,
)
from tests.fixture_factory import mark_dictionary_complete


def _write_qingping_style_fixture(path: Path) -> None:
    rules = load_rule_pack(Path(__file__).resolve().parents[1])
    workbook = Workbook()
    detail = workbook.active
    detail.title = "明细"
    detail.append(["日期", "凭证字", "凭证号", "分录行号", "摘要", "科目编码", "科目名称", "借方", "贷方", None, "流量金额（原币）", None, "主表项目名称"])
    detail.append(["2026/2/27", "记", 231, 11, "财务应付", "2202.03", "应付账款_财务", 58972968.30, None, None, 58972968.30, None, "购建固定资产、无形资产和其他长期资产支付的现金"])
    detail.append(["2026/2/27", "记", 231, 13, "5-票据红冲", "1121.01", "应收票据_银行承兑汇票", None, 48972968.30, None, -48972968.30, None, "购建固定资产、无形资产和其他长期资产支付的现金"])
    detail.append(["2026/1/31", "记", 176, 6, "计提2026.1月工资", "2211.01", "应付职工薪酬_工资", None, 926170.61, None, 926170.61, None, "购买商品、接受劳务支付的现金"])
    statement = workbook.create_sheet("现流表")
    statement.append(["单位名称：测试主体", None, "未审数"])
    statement.append(["项目", "行次", "2026年1-6月"])
    opening = 10926170.61
    row = 3
    for item in rules.statement_items:
        value = 0
        if item.item_id == "CASH-OPENING":
            value = opening
        elif item.item_id == "CASH-CLOSING":
            value = 0
        statement.cell(row, 1, item.name)
        statement.cell(row, 2, item.display_order)
        statement.cell(row, 3, value)
        row += 1
    workbook.save(path)


class QingpingRegressionTests(unittest.TestCase):
    def test_qingping_style_single_sided_detail_reconciles_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "清平式单边明细.xlsx"
            _write_qingping_style_fixture(source)
            preflight = run_preflight(
                [source],
                ("100000", "50000", "5000"),
                output_parent=root,
                statement_path=source,
            )
            confirm_cash_scope(preflight.run_dir, {})
            # 本用例不涉及词典机制，直接标记齐备（门禁通行）
            mark_dictionary_complete(preflight.run_dir)
            classified = run_classification(preflight.run_dir)
            # 重构后口径：本夹具组成均达整体重要性，跳过逐笔 AI（大额强制人工复核兜底）
            self.assertEqual(0, classified.ai_tasks_missing)
            state_path = preflight.run_dir / "计算留痕数据" / "运行状态.json"
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual("相符", state["rough_reconciliation"]["status"])
            self.assertEqual(-10_926_170_61, state["rough_reconciliation"]["detail_sum_cent"])
            ai_results = root / "AI结果.jsonl"
            ai_results.write_text(
                "".join(
                    json.dumps(
                        {
                            "task_id": task["task_id"],
                            "component_id": task["component_id"],
                            "item_id": task["system_item_id"],
                            "reason": "与系统首选一致",
                            "confidence": "high",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                    for task in state["ai_tasks"]
                ),
                encoding="utf-8-sig",
            )
            imported = import_ai_results(preflight.run_dir, ai_results)
            self.assertEqual("AI 已完成", imported.status)
            final = finalize_run(preflight.run_dir)
            self.assertNotIn("草稿", final.overall_status)
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            self.assertEqual(0, state["reconciliation"]["difference_cent"])
            self.assertEqual("现金流量表与货币资金变动的勾稽核对：相符", state["reconciliation"]["status"])
            workbook = load_workbook(final.workbook_path, data_only=False)
            try:
                self.assertIn("现金范围与现金流量表与货币资金变动的勾稽核对", workbook.sheetnames)
                hits = [
                    f"{sheet.title}!{cell.coordinate}"
                    for sheet in workbook.worksheets
                    for row in sheet.iter_rows()
                    for cell in row
                    if isinstance(cell.value, str) and "现金调节" in cell.value
                ]
                self.assertEqual([], hits)
            finally:
                workbook.close()


if __name__ == "__main__":
    unittest.main()
