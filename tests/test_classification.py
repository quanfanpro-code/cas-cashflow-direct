from __future__ import annotations

import unittest
from pathlib import Path

from cashflow_direct.classification import classify_component, load_rule_pack
from cashflow_direct.money import statement_amount_cent
from tests.fixture_factory import cashflow_component


ROOT = Path(__file__).resolve().parents[1]


class ClassificationTests(unittest.TestCase):
    def test_general_enterprise_pack_has_35_rows_and_22_leaf_items(self) -> None:
        rules = load_rule_pack(ROOT)
        self.assertEqual(35, len(rules.statement_items))
        expected_leaf_ids = {
            "CFO-01", "CFO-02", "CFO-03", "CFO-04", "CFO-05", "CFO-06", "CFO-07",
            "CFI-01", "CFI-02", "CFI-03", "CFI-04", "CFI-05", "CFI-06", "CFI-07", "CFI-08", "CFI-09",
            "CFF-01", "CFF-02", "CFF-03", "CFF-04", "CFF-05", "CFF-06",
        }
        self.assertEqual(expected_leaf_ids, {item.item_id for item in rules.statement_items if item.is_leaf})

    def test_ordinary_current_account_defaults_to_other_operating_cash(self) -> None:
        rules = load_rule_pack(ROOT)
        received = classify_component(cashflow_component("收到甲单位往来款", 500_000), rules)
        paid = classify_component(cashflow_component("支付乙单位往来款", -500_000), rules)
        self.assertEqual("CFO-03", received.system_item_id)
        self.assertEqual("CFO-07", paid.system_item_id)

    def test_explicit_financing_evidence_overrides_current_account_default(self) -> None:
        decision = classify_component(
            cashflow_component("收到股东借款", 5_000_000), load_rule_pack(ROOT)
        )
        self.assertEqual("CFF-02", decision.system_item_id)

    def test_major_business_boundaries_have_one_unique_system_choice(self) -> None:
        rules = load_rule_pack(ROOT)
        cases = (
            ("销售商品收到货款", 100, "CFO-01"),
            ("收到增值税留抵退税", 100, "CFO-02"),
            ("采购原材料付款", -100, "CFO-04"),
            ("发放职工工资", -100, "CFO-05"),
            ("缴纳企业所得税", -100, "CFO-06"),
            ("收回长期股权投资", 100, "CFI-01"),
            ("收到被投资单位分红", 100, "CFI-02"),
            ("处置固定资产收到款项", 100, "CFI-03"),
            ("处置子公司收到现金", 100, "CFI-04"),
            ("购买机器设备付款", -100, "CFI-06"),
            ("支付股权投资款", -100, "CFI-07"),
            ("取得子公司支付现金", -100, "CFI-08"),
            ("收到股东增资款", 100, "CFF-01"),
            ("取得银行借款", 100, "CFF-02"),
            ("偿还银行借款本金", -100, "CFF-04"),
            ("支付现金股利及借款利息", -100, "CFF-05"),
            ("收到其他投资活动款项", 100, "CFI-05"),
            ("支付其他投资活动款项", -100, "CFI-09"),
            ("收到其他筹资活动款项", 100, "CFF-03"),
            ("支付其他筹资活动款项", -100, "CFF-06"),
        )
        for summary, amount, expected in cases:
            with self.subTest(summary=summary):
                decision = classify_component(cashflow_component(summary, amount), rules)
                self.assertEqual(expected, decision.system_item_id)
                self.assertTrue(decision.matched_rule_id)
                self.assertTrue(decision.resolved)

    def test_refund_offsets_original_project_as_negative_statement_amount(self) -> None:
        rules = load_rule_pack(ROOT)
        refund = classify_component(cashflow_component("收到供应商退回采购款", 12_000), rules)
        independent = classify_component(cashflow_component("收到客户新销售款", 12_000), rules)
        self.assertEqual("CFO-04", refund.system_item_id)
        self.assertEqual(-12_000, statement_amount_cent(12_000, refund.normal_direction))
        self.assertEqual("CFO-01", independent.system_item_id)

    def test_zero_and_explicit_non_cash_components_are_excluded(self) -> None:
        rules = load_rule_pack(ROOT)
        zero = classify_component(cashflow_component("不涉及现金", 0), rules)
        internal = classify_component(
            cashflow_component("账户内部划转", 100, anomalies=("internal_transfer",)), rules
        )
        self.assertTrue(zero.excluded)
        self.assertTrue(internal.excluded)
        self.assertEqual("", zero.system_item_id)


if __name__ == "__main__":
    unittest.main()
