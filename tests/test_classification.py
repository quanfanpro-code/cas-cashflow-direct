from __future__ import annotations

import unittest
from pathlib import Path

import pytest

from cashflow_direct.account_dictionary import (
    AccountDictionary,
    AccountSemanticEntry,
    load_common_dictionary,
)
from cashflow_direct.classification import (
    classify_component as _classify_component,
    load_rule_pack,
    standardize_flow_item,
)
from cashflow_direct.summary_semantics import analyze_summary, load_summary_rules
from cashflow_direct.models import CashflowComponent
from cashflow_direct.validation import validate_classification
from cashflow_direct.money import statement_amount_cent
from tests.fixture_factory import cashflow_component


ROOT = Path(__file__).resolve().parents[1]


def classify_component(component, rules, dictionary=None, summary_semantics=None):
    """测试入口始终先形成正式摘要语义，禁止回退到旧关键词分类。"""
    semantics = summary_semantics or {
        component.summary: analyze_summary(component.summary, load_summary_rules(ROOT))
    }
    account_dictionary = dictionary or load_common_dictionary(ROOT)
    return _classify_component(component, rules, account_dictionary, semantics)


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

    def test_vat_accompanies_trade_classification(self) -> None:
        rules = load_rule_pack(ROOT)
        cases = [
            (cashflow_component("支付律师费", -298496, counterpart_accounts=("管理费用", "应交税费")), "CFO-07"),
            (cashflow_component("支付CS Fee", -152430, counterpart_accounts=("应付账款", "应交税费")), ""),
            (cashflow_component("收取物业租赁款", 209411655, counterpart_accounts=("预收账款", "应交税费")), "CFO-01"),
            (cashflow_component("缴纳增值税", -1000, counterpart_accounts=("应交税费",)), "CFO-06"),
            (cashflow_component("支付款项", -1000, counterpart_accounts=("应交税费",)), ""),
        ]
        for component, expected in cases:
            with self.subTest(summary=component.summary):
                self.assertEqual(expected, classify_component(component, rules).system_item_id)

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

    def test_each_exact_standard_leaf_label_is_only_for_comparison(self) -> None:
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
                self.assertEqual("", decision.system_item_id)
                self.assertEqual("NO-BUSINESS-CANDIDATE", decision.matched_rule_id)
                self.assertEqual(0, decision.evidence_score)
                self.assertEqual((), decision.candidate_item_ids)
                self.assertFalse(decision.resolved)

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

    def test_standard_original_label_does_not_create_business_evidence(self) -> None:
        decision = classify_component(
            cashflow_component(
                "普通业务",
                -100,
                ("普通科目",),
                original_item_text="支付的各项税费",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("", decision.system_item_id)
        self.assertEqual("NO-BUSINESS-CANDIDATE", decision.matched_rule_id)
        self.assertEqual(0, decision.evidence_score)
        self.assertIn("原项目只用于比较", decision.reason)

    def test_penalty_candidate_is_kept_separate_from_conflicting_original_label(self) -> None:
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
        self.assertTrue(decision.matched_rule_id.startswith("DICT-COMMON-"))
        self.assertEqual(55, decision.evidence_score)
        self.assertEqual("medium", decision.evidence_level)
        self.assertTrue(decision.sources_independent)
        self.assertFalse(decision.resolved)
        self.assertEqual("conflicts", decision.original_item_state)
        self.assertIn("滞纳金", decision.reason)
        self.assertIn("原项目", decision.reason)

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
        self.assertTrue(decision.matched_rule_id.startswith("SUMMARY-SEMANTICS-"))
        self.assertEqual("high", decision.evidence_level)
        self.assertEqual(90, decision.evidence_score)
        self.assertFalse(decision.resolved)
        self.assertIn("销售商品", decision.reason)
        self.assertIn("主营业务收入", decision.reason)
        self.assertIn("原项目能够标准化并与候选一致", decision.reason)
        self.assertNotIn("SUMMARY-SEMANTICS", decision.reason)

    def test_unlabeled_business_gap_does_not_invent_a_direction_fallback(self) -> None:
        decision = classify_component(
            cashflow_component("普通业务", -100, ("普通科目",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("", decision.system_item_id)
        self.assertEqual("NO-BUSINESS-CANDIDATE", decision.matched_rule_id)
        self.assertEqual(0, decision.evidence_score)
        self.assertIn("没有形成有效候选", decision.reason)

    def test_one_complete_path_uses_one_best_supported_candidate(self) -> None:
        decision = classify_component(
            cashflow_component(
                "购买机器设备并支付股权投资款",
                -100,
                ("固定资产", "长期股权投资"),
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("", decision.system_item_id)
        self.assertEqual("AMBIGUOUS-SOURCE-CANDIDATES", decision.matched_rule_id)
        # 摘要和完整路径各自都只能形成同一组弱候选，分别保留10分。
        self.assertEqual(20, decision.evidence_score)
        self.assertEqual(("CFI-06", "CFI-07"), decision.candidate_item_ids)
        self.assertFalse(decision.resolved)

    def test_guarantee_candidate_and_investing_original_are_recorded_as_conflicting(self) -> None:
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
        self.assertTrue(decision.matched_rule_id.startswith("SUMMARY-SEMANTICS-"))
        self.assertEqual(25, decision.evidence_score)
        self.assertEqual("low", decision.evidence_level)
        self.assertFalse(decision.resolved)
        self.assertEqual("conflicts", decision.original_item_state)

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
                self.assertFalse(decision.resolved)

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
        self.assertEqual(90, bank_interest.evidence_score)
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
        self.assertIsNone(zero.evidence_score)
        self.assertIsNone(internal.evidence_score)


    def test_original_label_conflict_is_recorded_without_replacing_candidate(self) -> None:
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
        self.assertTrue(decision.matched_rule_id.startswith("DICT-COMMON-"))
        self.assertEqual(55, decision.evidence_score)
        self.assertTrue(decision.sources_independent)
        self.assertFalse(decision.resolved)
        self.assertEqual("conflicts", decision.original_item_state)
        self.assertIn("原项目", decision.reason)

    def test_label_consistent_with_high_evidence_rule_keeps_exact(self) -> None:
        # 候选与原项目一致仍只完成候选阶段，后续动作由评分和重要性共同决定。
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
        self.assertTrue(decision.matched_rule_id.startswith("SUMMARY-SEMANTICS-"))
        self.assertFalse(decision.resolved)
        self.assertEqual("agrees", decision.original_item_state)
        self.assertIn("原项目能够标准化并与候选一致", decision.reason)

    def test_label_with_no_business_evidence_does_not_create_a_candidate(self) -> None:
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
        self.assertEqual("", decision.system_item_id)
        self.assertEqual("NO-BUSINESS-CANDIDATE", decision.matched_rule_id)
        self.assertEqual(0, decision.evidence_score)

    def test_new_terms_classify_correctly(self) -> None:
        # Task 2 Step 4 新增规则词条逐一核对
        rules = load_rule_pack(ROOT)
        cases = (
            ("支付税收滞纳金", -100, ("营业外支出",), "CFO-07"),
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

    def test_accounts_payable_alone_is_only_low_purchase_evidence(self) -> None:
        # 仅一级“应付账款”是宽泛往来性质，不能唯一指向采购付款。
        decision = classify_component(
            cashflow_component("普通业务", -100, ("应付账款",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("", decision.system_item_id)
        self.assertEqual("no_candidate", decision.candidate_status)
        self.assertEqual((), decision.candidate_item_ids)
        self.assertEqual("invalid", decision.evidence_level)
        self.assertEqual(0, decision.evidence_score)

    def test_pay_huokuan_summary_is_medium_purchase_evidence(self) -> None:
        # “支付+货款”只有动作和通用对象，缺少用途或交易属性，摘要最高25分。
        decision = classify_component(
            cashflow_component("支付货款", -100, ("应付账款_财务",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-04", decision.system_item_id)
        self.assertEqual("low", decision.evidence_level)
        self.assertEqual(25, decision.evidence_score)

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

        self.assertEqual("", decision.system_item_id)
        self.assertEqual("NO-BUSINESS-CANDIDATE", decision.matched_rule_id)
        self.assertEqual(0, decision.evidence_score)

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
        # 摘要与完整路径都指向偿付利息，形成两个独立来源。
        self.assertEqual("high", decision.evidence_level)

    def test_fixed_asset_clearing_and_original_label_do_not_create_a_candidate(self) -> None:
        decision = classify_component(
            cashflow_component(
                "地下电动铲运机评估费",
                -100,
                ("固定资产清理",),
                original_item_text="处置固定资产、无形资产和其他长期资产收回的现金净额",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("", decision.system_item_id)
        self.assertEqual("NO-BUSINESS-CANDIDATE", decision.matched_rule_id)

    def test_common_sales_collection_and_advance_refund_have_business_rules(self) -> None:
        rules = load_rule_pack(ROOT)
        receipt = classify_component(
            cashflow_component("销售收款", 100, ("合同负债_业务",)), rules
        )
        refund = classify_component(
            cashflow_component("预收退款", -100, ("合同负债_业务",)), rules
        )

        self.assertEqual("CFO-01", receipt.system_item_id)
        self.assertEqual(50, receipt.evidence_score)
        self.assertEqual("medium", receipt.evidence_level)
        self.assertEqual("CFO-01", refund.system_item_id)
        self.assertEqual(50, refund.evidence_score)
        self.assertEqual("medium", refund.evidence_level)

    def test_withheld_individual_income_tax_needs_employee_service_object(self) -> None:
        # “代扣个税”本身没有说明服务对象，不能先验假定为本企业职工薪酬。
        decision = classify_component(
            cashflow_component("代扣个人所得税", -100, ("应交税费_应交个人所得税",)),
            load_rule_pack(ROOT),
        )

        self.assertNotIn("CFO-05", decision.candidate_item_ids)
        self.assertEqual("", decision.system_item_id)

    def test_plain_individual_income_tax_payment_does_not_invent_staff_service(self) -> None:
        decision = classify_component(
            cashflow_component("缴纳个人所得税", -100, ("应交税费_应交个人所得税",)),
            load_rule_pack(ROOT),
        )

        self.assertNotIn("CFO-05", decision.candidate_item_ids)
        self.assertEqual("", decision.system_item_id)

    def test_short_individual_tax_word_does_not_invent_staff_service(self) -> None:
        decision = classify_component(
            cashflow_component("支付个税", -100, ("应交税费_个人所得税",)),
            load_rule_pack(ROOT),
        )

        self.assertNotIn("CFO-05", decision.candidate_item_ids)
        self.assertEqual("", decision.system_item_id)

    def test_dividend_individual_tax_is_not_staff_payment_without_service_object(self) -> None:
        decision = classify_component(
            cashflow_component("支付分红个税", -100, ("应交税费_个人所得税",)),
            load_rule_pack(ROOT),
        )

        self.assertNotIn("CFO-05", decision.candidate_item_ids)

    def test_enterprise_income_tax_still_uses_tax_payment_rule(self) -> None:
        # 回归保护：企业所得税仍命中 CFO-06 税费摘要词
        decision = classify_component(
            cashflow_component("缴纳企业所得税", -100, ("应交税费",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-06", decision.system_item_id)
        self.assertEqual(25, decision.evidence_score)
        self.assertEqual("low", decision.evidence_level)

    def test_note_discounting_without_financing_facts_has_no_candidate(self) -> None:
        # 会计类第1号指引及上交所案例：贴现不符合终止确认条件时应列筹资流入并确认为借款，
        # 不得高证据归入销售回款；证据不足时落低证据兜底并进入复核
        decision = classify_component(
            cashflow_component("票据贴现", 100, ("应收票据_银行承兑汇票",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("", decision.system_item_id)
        self.assertEqual("NO-BUSINESS-CANDIDATE", decision.matched_rule_id)
        self.assertEqual(0, decision.evidence_score)

    def test_note_maturity_collection_stays_sales_collection(self) -> None:
        # 回归保护：票据到期收款仍是销售回款
        decision = classify_component(
            cashflow_component("票据到期收款", 100, ("应收票据",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-01", decision.system_item_id)
        self.assertEqual(25, decision.evidence_score)
        self.assertEqual("low", decision.evidence_level)

    def test_wealth_management_redemption_principal_is_investment_recovery(self) -> None:
        # 应用指南三(二)1：理财/结构性存款赎回本金入"收回投资收到的现金"，
        # 只有收益部分才入"取得投资收益收到的现金"
        decision = classify_component(
            cashflow_component("赎回理财产品本金", 100, ("交易性金融资产",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-01", decision.system_item_id)
        self.assertEqual(90, decision.evidence_score)
        self.assertEqual("high", decision.evidence_level)

    def test_structured_deposit_interest_stays_investment_income(self) -> None:
        # 回归保护：结构性存款利息仍归 CFI-02，赎回排除词不能误伤收益场景
        decision = classify_component(
            cashflow_component("收到结构性存款利息", 100, ("结构性存款",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-02", decision.system_item_id)
        # 重构后口径：单摘要词45分=中
        self.assertEqual("medium", decision.evidence_level)

    def test_lease_liability_payment_is_other_financing_outflow(self) -> None:
        # 准则21号第53条：偿还租赁负债本金和利息的现金计入筹资活动
        decision = classify_component(
            cashflow_component("偿还租赁负债本金及利息", -100, ("租赁负债",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFF-06", decision.system_item_id)
        # 重构后口径：单摘要词45分=中
        self.assertEqual("medium", decision.evidence_level)

    def test_finance_lease_payment_is_other_financing_outflow(self) -> None:
        # 准则21号第53条：非简化处理的租赁付款计入筹资活动
        decision = classify_component(
            cashflow_component("支付融资租赁租金", -100, ("长期应付款",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFF-06", decision.system_item_id)
        # 动作加业务对象本身不重复充当决定性属性。
        self.assertEqual("low", decision.evidence_level)

    def test_installment_payment_for_asset_is_other_financing_outflow(self) -> None:
        # 应用指南：分期付款方式购建固定资产各期支付的现金计入筹资活动
        decision = classify_component(
            cashflow_component("分期付款购建设备款", -100, ("长期应付款",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFF-06", decision.system_item_id)
        # 重构后口径：单摘要词45分=中
        self.assertEqual("medium", decision.evidence_level)

    def test_simple_office_rent_stays_operating_fallback(self) -> None:
        # 回归保护：简化处理的普通经营租赁租金不进筹资活动
        decision = classify_component(
            cashflow_component("支付办公楼租金", -100, ("管理费用",)),
            load_rule_pack(ROOT),
        )

        self.assertNotEqual("CFF-06", decision.system_item_id)

    def test_deed_tax_follows_asset_acquisition(self) -> None:
        # 契税为取得不动产的直接相关税费，随资产购建归 CFI-06
        decision = classify_component(
            cashflow_component("缴纳契税", -100, ("应交税费_应交契税",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-06", decision.system_item_id)
        self.assertEqual(("CFI-06",), decision.candidate_item_ids)
        self.assertFalse(decision.source_conflict)
        self.assertEqual(25, decision.evidence_score)

    def test_vehicle_purchase_tax_follows_asset_acquisition(self) -> None:
        # 车辆购置税为购置车辆的直接相关税费，随资产购建归 CFI-06
        decision = classify_component(
            cashflow_component("缴纳车辆购置税", -100, ("应交税费_应交车辆购置税",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-06", decision.system_item_id)
        self.assertEqual(("CFI-06",), decision.candidate_item_ids)
        self.assertFalse(decision.source_conflict)
        self.assertEqual(45, decision.evidence_score)

    def test_returned_vat_credit_refund_is_tax_payment(self) -> None:
        # 应用指南：缴回留抵退税款属于"支付的各项税费"
        decision = classify_component(
            cashflow_component("缴回留抵退税款", -100, ("应交税费",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-06", decision.system_item_id)
        self.assertEqual(25, decision.evidence_score)
        self.assertEqual("low", decision.evidence_level)

    def test_received_vat_credit_refund_stays_tax_refund(self) -> None:
        # 回归保护：收到留抵退税款仍归"收到的税费返还"
        decision = classify_component(
            cashflow_component("收到留抵退税款", 100, ("应交税费",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-02", decision.system_item_id)
        self.assertEqual(25, decision.evidence_score)
        self.assertEqual("low", decision.evidence_level)


def test_complete_path_dictionary_recognizes_equipment_payable():
    """完整路径中的“应付设备款”按通用科目语义形成投资活动候选。"""
    rules = load_rule_pack(ROOT)
    component = CashflowComponent(
        component_id="CMP-LIFT", voucher_key="K", summary="付日立电梯公司电梯安装工程款",
        cash_delta_cent=-5000000, counterpart_accounts=("应付账款_应付设备款_暂估款",),
        original_item_text="购建固定资产、无形资产和其他长期资产支付的现金",
    )
    decision = classify_component(component, rules, load_common_dictionary(ROOT))
    assert decision.system_item_id == "CFI-06"
    assert decision.original_item_state == "agrees"
    assert decision.resolved is False
    assert decision.evidence_score == 45


def test_fee_type_split_categories():
    """明确业务属性的复合词才允许形成唯一费用类候选。"""
    rules = load_rule_pack(ROOT)
    cases = (
        ("审计费", ("管理费用",), "CFO-07"),
        ("生产外包服务费", ("生产成本",), "CFO-04"),
        ("工程监理费", ("在建工程",), "CFI-06"),
        ("融资顾问费", ("财务费用",), "CFF-06"),
        ("并购咨询费", ("管理费用",), "CFI-07"),
    )
    for summary, counterparts, expected in cases:
        decision = classify_component(
            cashflow_component(summary, -100, counterparts), rules
        )
        assert decision.system_item_id == expected, f"{summary} -> {decision.system_item_id}"


@pytest.mark.parametrize("summary", ("支付咨询费", "支付服务费"))
def test_plain_consulting_or_service_fee_stays_ambiguous(summary: str):
    """用途不明的裸费用词不能机械回落到经营活动。"""
    decision = classify_component(
        cashflow_component(summary, -100, ("普通往来科目",)),
        load_rule_pack(ROOT),
    )

    assert decision.system_item_id == ""
    assert decision.candidate_status == "ambiguous"
    assert len(decision.candidate_item_ids) > 1
    assert decision.evidence_score == 10


def test_plain_vat_in_purchase_summary_is_not_tax_payment():
    """复核修复：裸"增值税"不再直接指向税费缴纳，采购摘要带"含增值税"归购买商品且不再冲突。"""
    decision = classify_component(
        cashflow_component("支付货款（含增值税）", -1130000, ("原材料_钢材",)),
        load_rule_pack(ROOT),
    )
    assert decision.system_item_id == "CFO-04"
    assert decision.evidence_score == 55
    assert decision.resolved is False


def test_explicit_vat_payment_is_tax_payment():
    """回归保护：摘要与完整路径共同支持时，缴纳增值税仍形成高证据候选。"""
    decision = classify_component(
        cashflow_component("缴纳增值税", -340000, ("应交税费_未交增值税",)),
        load_rule_pack(ROOT),
    )
    assert decision.system_item_id == "CFO-06"
    assert decision.summary_quality == 25


def test_individual_tax_without_service_object_does_not_conflict_with_construction_path():
    """个税服务对象不明时，不虚构职工候选与资本化路径制造冲突。"""
    decision = classify_component(
        cashflow_component("代扣个税", -50000, ("在建工程_资本化",)),
        load_rule_pack(ROOT),
    )
    assert set(decision.candidate_item_ids) == {"CFI-06"}
    assert decision.source_conflict is False
    assert decision.account_path_quality == 25


def test_construction_wages_are_a_two_source_conflict():
    """摘要指向职工、完整路径指向在建工程时，必须直接识别为来源冲突。"""
    decision = classify_component(
        cashflow_component("发放工资", -200000, ("在建工程_厂房",)),
        load_rule_pack(ROOT),
    )
    assert set(decision.candidate_item_ids) == {"CFO-05", "CFI-06"}
    assert decision.matched_rule_id == "BUSINESS-RULE-CONFLICT"
    assert decision.source_conflict is True
    assert decision.evidence_score is None
    assert decision.resolved is False


def test_unknown_transfer_does_not_use_a_direction_fallback():
    """摘要和路径都没有业务事实时，不因现金方向虚构候选。"""
    decision = classify_component(
        cashflow_component("转账", -10000, ("其他科目_杂项",)),
        load_rule_pack(ROOT),
    )
    assert decision.system_item_id == ""
    assert decision.matched_rule_id == "NO-BUSINESS-CANDIDATE"
    assert decision.evidence_score == 0


def test_dictionary_hit_reason_includes_note_id():
    """复核修复：经确认的公司特殊规则命中时，理由必须留 NOTE 编号痕迹。"""
    dictionary = AccountDictionary((
        AccountSemanticEntry("其他应付款_回购义务", "筹资", "CFF-06", "依据", "high", "custom", "NOTE-01"),
    ))
    decision = classify_component(
        cashflow_component("支付款项", -100, ("其他应付款_回购义务",)),
        load_rule_pack(ROOT),
        dictionary,
    )
    assert decision.system_item_id == "CFF-06"
    assert "依据公司特殊规则：NOTE-01" in decision.reason


if __name__ == "__main__":
    unittest.main()
