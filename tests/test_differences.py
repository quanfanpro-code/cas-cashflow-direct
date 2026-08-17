from __future__ import annotations

import unittest
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
        self.assertEqual(
            {
                "日期": "2026-01-01",
                "凭证字": "记",
                "凭证号": "1",
                "摘要": "匿名业务",
                "科目编码": "1002.01",
                "科目名称": "银行存款",
                "借方": 100.0,
                "贷方": None,
                "流量金额（原币）": 100.0,
                "主表项目名称": "支付的各项税费",
                "对方科目": "其他应付款",
                "原项目标准化结果": "支付的各项税费",
                "自动判定现流项目": "支付其他与经营活动有关的现金",
                "差异说明": "标准项目不一致",
                "来源文件": "匿名输入.xlsx",
                "来源工作表": "明细",
                "来源单元格": "A2:L2",
            },
            rows[0],
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
        self.assertEqual("原项目无法标准化", rows[0]["差异说明"])
        self.assertEqual("不进入正表", rows[1]["自动判定现流项目"])
        self.assertEqual("自动判定不进入正表", rows[1]["差异说明"])

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
            all("多个自动判定结果" in str(row["差异说明"]) for row in rows)
        )


if __name__ == "__main__":
    unittest.main()
