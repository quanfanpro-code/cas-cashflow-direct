from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from cashflow_direct.classification import load_rule_pack
from cashflow_direct.differences import build_original_auto_differences
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    NormalizedEntry,
    SourceLocator,
)


ROOT = Path(__file__).resolve().parents[1]


def entry(
    entry_id: str,
    original_item: str,
    *,
    row: int = 2,
    account_name: str = "银行存款",
) -> NormalizedEntry:
    return NormalizedEntry(
        entry_id=entry_id,
        source=SourceLocator("F1", "明细", row, row, f"A{row}:L{row}"),
        voucher_key="V1",
        voucher_date="2026-01-01",
        voucher_no="1",
        summary="匿名业务",
        account_name=account_name,
        counterpart_name="其他应付款",
        debit_cent=10_000,
        credit_cent=0,
        flow_amount_cent=10_000,
        original_flow_item=original_item,
        voucher_word="记",
        account_code="1002.01",
        source_debit_cent=10_000,
        source_credit_cent=None,
        source_flow_amount_cent=10_000,
    )


def component(
    component_id: str,
    original_item: str,
    source_keys: tuple[str, ...],
) -> CashflowComponent:
    return CashflowComponent(
        component_id=component_id,
        voucher_key="V1",
        summary="匿名业务",
        cash_delta_cent=10_000,
        original_item_text=original_item,
        source_keys=source_keys,
    )


def decision(
    component_id: str,
    item_id: str,
    item_name: str,
    *,
    excluded: bool = False,
) -> ClassificationDecision:
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=item_id,
        system_item_name=item_name,
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="测试判断",
        evidence_level="high",
        excluded=excluded,
        evidence_score=70,
        summary_quality=45,
        account_path_quality=25,
        sources_independent=True,
        decision_action="automatic_change",
        materiality_level="M0",
    )


class OriginalAutoDifferenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rules = load_rule_pack(ROOT)

    def build(
        self,
        entries: tuple[NormalizedEntry, ...],
        components: tuple[CashflowComponent, ...],
        decisions: tuple[ClassificationDecision, ...],
        internal: frozenset[str] = frozenset(),
    ) -> tuple[dict[str, object], ...]:
        return build_original_auto_differences(
            entries,
            components,
            decisions,
            internal,
            self.rules,
            {"F1": "匿名输入.xlsx"},
        )

    def test_standardized_equal_item_is_not_a_difference(self) -> None:
        source = entry("E1", " 支付的各项税费，。 ")
        current = component("C1", source.original_flow_item, ("E1",))
        automatic = decision("C1", "CFO-06", "支付的各项税费")

        self.assertEqual((), self.build((source,), (current,), (automatic,)))

    def test_different_item_keeps_every_requested_source_field(self) -> None:
        source = entry("E1", "支付的各项税费")
        current = component("C1", source.original_flow_item, ("E1",))
        automatic = decision("C1", "CFO-07", "支付其他与经营活动有关的现金")

        rows = self.build((source,), (current,), (automatic,))

        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertNotIn("主表项目名称", row)
        self.assertNotIn("最终决定现流项目", row)
        self.assertEqual("支付的各项税费", row["原项目标准化结果"])
        self.assertEqual("支付其他与经营活动有关的现金", row["审定现流表项目"])
        self.assertEqual(
            "证据得分70分；金额档位为低于明显微小错报临界值；符合自动修改条件。",
            row["差异形成原因"],
        )
        self.assertEqual("摘要“匿名业务”；强证据45分", row["独立来源1"])
        self.assertEqual(
            "完整对方科目路径“路径为空”；中等证据25分",
            row["独立来源2"],
        )

    def test_unstandardized_and_internal_transfer_rows_are_visible(self) -> None:
        custom = entry("E1", "客户自定义项目")
        custom_component = component("C1", custom.original_flow_item, ("E1",))
        custom_decision = decision("C1", "CFO-03", "收到其他与经营活动有关的现金")
        transfer = entry("E2", "支付其他与经营活动有关的现金", row=3)

        rows = self.build(
            (custom, transfer),
            (custom_component,),
            (custom_decision,),
            frozenset({"E2"}),
        )

        self.assertEqual(2, len(rows))
        self.assertEqual("原项目无法标准化", rows[0]["原项目标准化结果"])
        self.assertIn("证据得分70分", rows[0]["差异形成原因"])
        self.assertEqual("不进入正表", rows[1]["审定现流表项目"])
        self.assertIn("内部划转", rows[1]["差异形成原因"])

    def test_multiple_source_rows_expand_without_common_leg_duplication(self) -> None:
        first = entry("E1", "支付的各项税费", row=2)
        second = entry("E2", "支付的各项税费", row=3)
        other = entry("E3", "支付其他与经营活动有关的现金", row=4)
        tax_component = component("C1", first.original_flow_item, ("E1", "E2"))
        other_component = component("C2", other.original_flow_item, ("E1", "E3"))
        tax_decision = decision("C1", "CFO-07", "支付其他与经营活动有关的现金")
        other_decision = decision("C2", "CFO-07", "支付其他与经营活动有关的现金")

        rows = self.build(
            (first, second, other),
            (tax_component, other_component),
            (tax_decision, other_decision),
        )

        self.assertEqual(["A2:L2", "A3:L3"], [row["来源单元格"] for row in rows])

    def test_one_source_with_two_real_auto_results_keeps_both(self) -> None:
        source = entry("E1", "支付的各项税费")
        first = component("C1", source.original_flow_item, ("E1",))
        second = component("C2", source.original_flow_item, ("E1",))
        decisions = (
            decision("C1", "CFO-07", "支付其他与经营活动有关的现金"),
            decision("C2", "CFI-09", "支付其他与投资活动有关的现金"),
        )

        rows = self.build((source,), (first, second), decisions)

        self.assertEqual(2, len(rows))
        self.assertTrue(
            all("拆分为多个业务组成" in str(row["差异形成原因"]) for row in rows)
        )

    def test_pending_human_result_is_not_a_system_decision_difference(self) -> None:
        source = entry("E1", "支付的各项税费")
        current = component("C1", source.original_flow_item, ("E1",))
        pending_human = replace(
            decision("C1", "CFO-07", "支付其他与经营活动有关的现金"),
            resolved=False,
            decision_action="human_decision",
            decision_source="candidate",
        )

        self.assertEqual((), self.build((source,), (current,), (pending_human,)))

    def test_pending_ai_and_manual_results_are_not_current_system_differences(self) -> None:
        source = entry("E1", "支付的各项税费")
        current = component("C1", source.original_flow_item, ("E1",))
        pending_ai = replace(
            decision("C1", "CFO-07", "支付其他与经营活动有关的现金"),
            resolved=False,
            decision_action="ai_review",
            decision_source="candidate",
        )
        manual = replace(
            decision("C1", "CFO-07", "支付其他与经营活动有关的现金"),
            decision_source="manual",
        )

        self.assertEqual((), self.build((source,), (current,), (pending_ai,)))
        self.assertEqual((), self.build((source,), (current,), (manual,)))

    def test_internal_transfer_explains_pairing_and_marks_score_not_applicable(self) -> None:
        incoming = replace(
            entry("E1", "", row=2, account_name="银行存款_一般户"),
            flow_amount_cent=0,
            source_flow_amount_cent=None,
        )
        outgoing = replace(
            entry("E2", "收到其他与经营活动有关的现金", row=3, account_name="其他货币资金"),
            debit_cent=0,
            credit_cent=10_000,
            source_debit_cent=None,
            source_credit_cent=10_000,
        )

        rows = self.build(
            (incoming, outgoing),
            (),
            (),
            frozenset({"E1", "E2"}),
        )

        self.assertEqual(1, len(rows))
        row = rows[0]
        self.assertIn("两个已确认纳入现金范围的账户", row["差异形成原因"])
        self.assertIn("方向相反且金额相等", row["差异形成原因"])
        self.assertEqual(
            "不适用：内部划转在现金范围阶段判定，不进入现金流项目分类评分。",
            row["打分逻辑描述及打分结果"],
        )
        self.assertIn("银行存款_一般户", row["独立来源1"])
        self.assertIn("其他货币资金", row["独立来源1"])
        self.assertIn("A2:L2", row["独立来源2"])
        self.assertIn("A3:L3", row["独立来源2"])

    def test_auto_fill_for_blank_original_is_a_difference_result(self) -> None:
        source = entry("E1", "")
        current = component("C1", "", ("E1",))
        automatic = decision("C1", "CFO-07", "支付其他与经营活动有关的现金")

        rows = self.build((source,), (current,), (automatic,))

        self.assertEqual(1, len(rows))
        self.assertEqual("原项目为空", rows[0]["原项目标准化结果"])
        self.assertEqual(
            "支付其他与经营活动有关的现金",
            rows[0]["审定现流表项目"],
        )

    def test_difference_explains_score_and_amount_tier_in_natural_language(self) -> None:
        source = entry("E1", "支付的各项税费")
        current = component("C1", source.original_flow_item, ("E1",))
        automatic = decision("C1", "CFO-07", "支付其他与经营活动有关的现金")

        row = self.build((source,), (current,), (automatic,))[0]

        self.assertNotIn("M0", row["差异形成原因"])
        self.assertIn("低于明显微小错报临界值", row["差异形成原因"])
        self.assertEqual(
            "摘要为强证据45分，完整对方科目路径为中等证据25分；"
            "两个来源相互独立并共同支持同一项目，合计70分。",
            row["打分逻辑描述及打分结果"],
        )


if __name__ == "__main__":
    unittest.main()
