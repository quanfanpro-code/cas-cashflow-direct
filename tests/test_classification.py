from __future__ import annotations

import unittest
from pathlib import Path

from cashflow_direct.classification import (
    classify_component,
    load_rule_pack,
    standardize_flow_item,
)
from cashflow_direct.validation import validate_classification
from cashflow_direct.money import statement_amount_cent
from tests.fixture_factory import cashflow_component


ROOT = Path(__file__).resolve().parents[1]


class ClassificationTests(unittest.TestCase):
    def test_standardize_flow_item_uses_leaf_names_and_ignores_format_punctuation(self) -> None:
        rules = load_rule_pack(ROOT)

        standardized = standardize_flow_item(
            " 支付的各项税费，。 ", rules
        )

        self.assertIsNotNone(standardized)
        self.assertEqual("CFO-06", standardized.item_id)
        self.assertIsNone(standardize_flow_item("客户自定义项目", rules))
        self.assertIsNone(standardize_flow_item("经营活动现金流入小计", rules))

    def test_empty_component_set_cannot_pass_classification_validation(self) -> None:
        result = validate_classification((), ())
        self.assertFalse(result.valid)
        self.assertIn("未生成现金流业务组成", result.errors)

    def test_general_enterprise_pack_has_35_rows_and_22_leaf_items(self) -> None:
        rules = load_rule_pack(ROOT)
        self.assertEqual(35, len(rules.statement_items))
        expected_leaf_ids = {
            "CFO-01", "CFO-02", "CFO-03", "CFO-04", "CFO-05", "CFO-06", "CFO-07",
            "CFI-01", "CFI-02", "CFI-03", "CFI-04", "CFI-05", "CFI-06", "CFI-07", "CFI-08", "CFI-09",
            "CFF-01", "CFF-02", "CFF-03", "CFF-04", "CFF-05", "CFF-06",
        }
        self.assertEqual(expected_leaf_ids, {item.item_id for item in rules.statement_items if item.is_leaf})

    def test_each_exact_standard_leaf_label_can_be_used_as_a_low_evidence_fallback(self) -> None:
        rules = load_rule_pack(ROOT)
        for item in (item for item in rules.statement_items if item.is_leaf):
            amount = 100 if item.normal_direction == "inflow" else -100
            component = cashflow_component(
                "普通业务",
                amount,
                original_item_text=item.name,
                component_id=f"LABEL-{item.item_id}",
            )
            with self.subTest(item_id=item.item_id):
                decision = classify_component(component, rules)
                self.assertEqual(item.item_id, decision.system_item_id)
                self.assertEqual("ORIGINAL-LABEL-FALLBACK", decision.matched_rule_id)
                self.assertEqual("low", decision.evidence_level)

    def test_exact_standard_label_does_not_ignore_meaningful_words(self) -> None:
        rules = load_rule_pack(ROOT)
        item = rules.item_by_id["CFI-03"]
        shortened = item.name.replace("和", "", 1)
        decision = classify_component(
            cashflow_component(
                "处置长期资产收款",
                100,
                original_item_text=shortened,
                component_id="LABEL-NOT-EXACT",
            ),
            rules,
        )
        self.assertNotEqual("EXACT-STANDARD-LABEL", decision.matched_rule_id)

    def test_standard_original_label_is_only_a_low_evidence_fallback(self) -> None:
        decision = classify_component(
            cashflow_component(
                "普通业务",
                -100,
                ("普通科目",),
                original_item_text="支付的各项税费",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-06", decision.system_item_id)
        self.assertEqual("ORIGINAL-LABEL-FALLBACK", decision.matched_rule_id)
        self.assertEqual("low", decision.evidence_level)
        self.assertIn("保底分类", decision.reason)
        self.assertNotIn("完全一致", decision.reason)

    def test_high_business_evidence_beats_a_standard_but_wrong_original_label(self) -> None:
        decision = classify_component(
            cashflow_component(
                "税收滞纳金",
                -100,
                ("营业外支出_罚款、滞纳金",),
                original_item_text="支付的各项税费",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-07", decision.system_item_id)
        self.assertEqual("LABEL-BUSINESS-HIGH-CONFLICT", decision.matched_rule_id)
        self.assertIn("滞纳金", decision.reason)
        self.assertIn("原标签", decision.reason)

    def test_business_reason_stays_primary_when_original_label_agrees(self) -> None:
        decision = classify_component(
            cashflow_component(
                "销售商品收到货款",
                100,
                ("主营业务收入",),
                original_item_text="销售商品、提供劳务收到的现金",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-01", decision.system_item_id)
        self.assertEqual("CFO-01-SALES", decision.matched_rule_id)
        self.assertEqual("high", decision.evidence_level)
        self.assertIn("销售商品", decision.reason)
        self.assertIn("主营业务收入", decision.reason)
        self.assertIn("原标签一致，仅作补充", decision.reason)
        self.assertNotIn("CFO-01-SALES", decision.reason)

    def test_unlabeled_business_gap_uses_a_readable_direction_fallback(self) -> None:
        decision = classify_component(
            cashflow_component("普通业务", -100, ("普通科目",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-07", decision.system_item_id)
        self.assertEqual("CFO-07-FALLBACK", decision.matched_rule_id)
        self.assertEqual("low", decision.evidence_level)
        self.assertIn("现金为流出", decision.reason)
        self.assertIn("证据较弱", decision.reason)

    def test_different_high_business_rules_are_reported_as_a_conflict(self) -> None:
        decision = classify_component(
            cashflow_component(
                "购买机器设备并支付股权投资款",
                -100,
                ("固定资产", "长期股权投资"),
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-06", decision.system_item_id)
        self.assertEqual("BUSINESS-RULE-CONFLICT", decision.matched_rule_id)
        self.assertIn("购建固定资产、无形资产和其他长期资产支付的现金", decision.reason)
        self.assertIn("投资支付的现金", decision.reason)

    def test_medium_business_evidence_beats_the_original_label_without_becoming_high(self) -> None:
        decision = classify_component(
            cashflow_component(
                "支付保证金",
                -100,
                ("其他应收款",),
                original_item_text="支付其他与投资活动有关的现金",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-07", decision.system_item_id)
        self.assertEqual("LABEL-BUSINESS-MEDIUM-CONFLICT", decision.matched_rule_id)
        self.assertEqual("medium", decision.evidence_level)

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

    def test_bank_deposit_interest_is_operating_but_investment_interest_is_investing(self) -> None:
        rules = load_rule_pack(ROOT)
        bank_interest = classify_component(
            cashflow_component("收到银行存款利息", 12_000, ("财务费用-利息收入",)), rules
        )
        investment_interest = classify_component(
            cashflow_component("收到债券投资利息", 12_000, ("投资收益",)), rules
        )
        self.assertEqual("CFO-03", bank_interest.system_item_id)
        self.assertEqual("high", bank_interest.evidence_level)
        self.assertEqual("CFI-02", investment_interest.system_item_id)

    def test_customer_name_containing_investment_does_not_block_sales_receipt(self) -> None:
        decision = classify_component(
            cashflow_component("销售商品收到货款", 12_000, ("甲方投资有限公司",)),
            load_rule_pack(ROOT),
        )
        self.assertEqual("CFO-01", decision.system_item_id)

    def test_common_sales_and_purchase_refunds_offset_original_projects(self) -> None:
        rules = load_rule_pack(ROOT)
        sales_refund = classify_component(cashflow_component("退回客户货款", -12_000), rules)
        purchase_refund = classify_component(cashflow_component("收到退回采购款", 12_000), rules)
        self.assertEqual("CFO-01", sales_refund.system_item_id)
        self.assertEqual("CFO-04", purchase_refund.system_item_id)

    def test_combined_principal_and_interest_uses_each_counterpart_account(self) -> None:
        rules = load_rule_pack(ROOT)
        principal = classify_component(
            cashflow_component("偿还本金及利息", -100_000, ("短期借款",)), rules
        )
        interest = classify_component(
            cashflow_component("偿还本金及利息", -10_000, ("应付利息",)), rules
        )
        self.assertEqual("CFF-04", principal.system_item_id)
        self.assertEqual("CFF-05", interest.system_item_id)

    def test_zero_and_explicit_non_cash_components_are_excluded(self) -> None:
        rules = load_rule_pack(ROOT)
        zero = classify_component(cashflow_component("不涉及现金", 0), rules)
        internal = classify_component(
            cashflow_component("账户内部划转", 100, anomalies=("internal_transfer",)), rules
        )
        self.assertTrue(zero.excluded)
        self.assertTrue(internal.excluded)
        self.assertEqual("", zero.system_item_id)


    def test_label_conflict_with_high_evidence_rule_uses_the_business_candidate(self) -> None:
        # 原标签与高证据业务规则冲突时，业务项目是自动候选，标签只作备选。
        rules = load_rule_pack(ROOT)
        decision = classify_component(
            cashflow_component(
                "税收滞纳金",
                -100,
                ("营业外支出_罚款、滞纳金",),
                original_item_text="支付的各项税费",
                component_id="CFL-1",
            ),
            rules,
        )
        self.assertEqual("CFO-07", decision.system_item_id)
        self.assertEqual("LABEL-BUSINESS-HIGH-CONFLICT", decision.matched_rule_id)
        self.assertTrue(decision.resolved)
        self.assertIn("原标签", decision.reason)

    def test_label_consistent_with_high_evidence_rule_keeps_exact(self) -> None:
        # 规则与标签一致 → 维持 EXACT-STANDARD-LABEL
        rules = load_rule_pack(ROOT)
        decision = classify_component(
            cashflow_component(
                "销售商品收到货款",
                100,
                ("主营业务收入",),
                original_item_text="销售商品、提供劳务收到的现金",
                component_id="CFL-2",
            ),
            rules,
        )
        self.assertEqual("CFO-01", decision.system_item_id)
        self.assertEqual("CFO-01-SALES", decision.matched_rule_id)
        self.assertTrue(decision.resolved)
        self.assertIn("原标签一致，仅作补充", decision.reason)

    def test_label_with_no_business_evidence_is_a_low_evidence_fallback(self) -> None:
        rules = load_rule_pack(ROOT)
        decision = classify_component(
            cashflow_component(
                "普通业务",
                100,
                original_item_text="收到其他与经营活动有关的现金",
                component_id="CFL-3",
            ),
            rules,
        )
        self.assertEqual("ORIGINAL-LABEL-FALLBACK", decision.matched_rule_id)
        self.assertEqual("low", decision.evidence_level)

    def test_new_terms_classify_correctly(self) -> None:
        # Task 2 Step 4 新增规则词条逐一核对
        rules = load_rule_pack(ROOT)
        cases = (
            ("税收滞纳金", -100, ("营业外支出",), "CFO-07"),
            ("缴纳车船税", -100, ("应交税费",), "CFO-06"),
            ("收到结构性存款利息", 100, ("结构性存款",), "CFI-02"),
            ("支付长期待摊费用装修款", -100, ("长期待摊费用",), "CFI-06"),
            ("支付电费基金", -100, ("生产成本",), "CFO-04"),
        )
        for summary, amount, counterparts, expected in cases:
            with self.subTest(summary=summary):
                decision = classify_component(
                    cashflow_component(summary, amount, counterparts), rules
                )
                self.assertEqual(expected, decision.system_item_id)

    def test_accounts_payable_alone_is_only_medium_purchase_evidence(self) -> None:
        decision = classify_component(
            cashflow_component("支付服务费", -100, ("应付账款_财务",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-04", decision.system_item_id)
        self.assertEqual("medium", decision.evidence_level)

    def test_input_vat_does_not_turn_a_purchase_payment_into_tax_payment(self) -> None:
        decision = classify_component(
            cashflow_component(
                "采购设备进项税额",
                -100,
                ("应交税费_应交增值税_进项税额",),
                original_item_text="购建固定资产、无形资产和其他长期资产支付的现金",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-06", decision.system_item_id)
        self.assertEqual("ORIGINAL-LABEL-FALLBACK", decision.matched_rule_id)
        self.assertEqual("low", decision.evidence_level)

    def test_loan_interest_expense_is_not_treated_as_principal_repayment(self) -> None:
        decision = classify_component(
            cashflow_component(
                "偿还银行贷款利息",
                -100,
                ("财务费用_利息支出_长期借款利息",),
                original_item_text="分配股利、利润或偿付利息支付的现金",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFF-05", decision.system_item_id)
        self.assertEqual("high", decision.evidence_level)

    def test_fixed_asset_clearing_does_not_mean_cash_was_used_to_build_an_asset(self) -> None:
        decision = classify_component(
            cashflow_component(
                "地下电动铲运机评估费",
                -100,
                ("固定资产清理",),
                original_item_text="处置固定资产、无形资产和其他长期资产收回的现金净额",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-03", decision.system_item_id)
        self.assertEqual("ORIGINAL-LABEL-FALLBACK", decision.matched_rule_id)

    def test_common_sales_collection_and_advance_refund_have_business_rules(self) -> None:
        rules = load_rule_pack(ROOT)
        receipt = classify_component(
            cashflow_component("销售收款", 100, ("合同负债_业务",)), rules
        )
        refund = classify_component(
            cashflow_component("预收退款", -100, ("合同负债_业务",)), rules
        )

        self.assertEqual("CFO-01", receipt.system_item_id)
        self.assertEqual("high", receipt.evidence_level)
        self.assertEqual("CFO-01", refund.system_item_id)
        self.assertEqual("high", refund.evidence_level)


if __name__ == "__main__":
    unittest.main()
