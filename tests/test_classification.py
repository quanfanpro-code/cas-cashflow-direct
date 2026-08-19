from __future__ import annotations

import unittest
from pathlib import Path

from cashflow_direct.account_dictionary import AccountDictionary, AccountSemanticEntry
from cashflow_direct.classification import (
    _rule_matches,
    ClassificationRule,
    classify_component,
    load_rule_pack,
    standardize_flow_item,
)
from cashflow_direct.models import CashflowComponent
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

    def test_sole_account_terms_matches_only_when_sole_counterpart(self) -> None:
        rule = ClassificationRule(
            rule_id="CFO-06-SOLE",
            item_id="CFO-06",
            priority=20,
            direction="outflow",
            summary_terms=(),
            account_terms=(),
            exclude_terms=(),
            account_exclude_terms=(),
            evidence_level="medium",
            sole_account_terms=("应交税费",),
        )
        component = CashflowComponent(
            component_id="C1",
            voucher_key="V1",
            summary="支付款项",
            cash_delta_cent=-1000,
            counterpart_accounts=("应交税费",),
        )
        self.assertTrue(_rule_matches(rule, component))

        component_with_trade = CashflowComponent(
            component_id="C2",
            voucher_key="V2",
            summary="支付律师费",
            cash_delta_cent=-1000,
            counterpart_accounts=("应交税费", "管理费用"),
        )
        self.assertFalse(_rule_matches(rule, component_with_trade))

        inflow = CashflowComponent(
            component_id="C3",
            voucher_key="V3",
            summary="收到利息",
            cash_delta_cent=1000,
            counterpart_accounts=("应交税费",),
        )
        self.assertFalse(_rule_matches(rule, inflow))

    def test_vat_accompanies_trade_classification(self) -> None:
        rules = load_rule_pack(ROOT)
        cases = [
            (cashflow_component("支付律师费", -298496, counterpart_accounts=("管理费用", "应交税费")), "CFO-07"),
            (cashflow_component("支付CS Fee", -152430, counterpart_accounts=("应付账款", "应交税费")), "CFO-04"),
            (cashflow_component("收取物业租赁款", 209411655, counterpart_accounts=("预收账款", "应交税费")), "CFO-01"),
            (cashflow_component("缴纳增值税", -1000, counterpart_accounts=("应交税费",)), "CFO-06"),
            (cashflow_component("支付款项", -1000, counterpart_accounts=("应交税费",)), "CFO-06"),
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

    def test_insufficient_evidence_keeps_original_label_for_penalty(self) -> None:
        # 重构后口径：税收滞纳金证据打分50分<70，按新推翻门槛不再改判原标签，保留原标签送复核
        decision = classify_component(
            cashflow_component(
                "税收滞纳金",
                -100,
                ("营业外支出_罚款、滞纳金",),
                original_item_text="支付的各项税费",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-06", decision.system_item_id)
        self.assertEqual("LABEL-KEPT-INSUFFICIENT-EVIDENCE", decision.matched_rule_id)
        self.assertEqual("medium", decision.evidence_level)
        self.assertFalse(decision.resolved)
        self.assertTrue(decision.label_kept)
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
        self.assertEqual("medium", decision.evidence_level)
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

    def test_insufficient_evidence_keeps_investing_label(self) -> None:
        # 重构后口径：中证据（50分）不足以推翻原投资标签，保留原标签并送复核
        decision = classify_component(
            cashflow_component(
                "支付保证金",
                -100,
                ("其他应收款",),
                original_item_text="支付其他与投资活动有关的现金",
            ),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-09", decision.system_item_id)
        self.assertEqual("LABEL-KEPT-INSUFFICIENT-EVIDENCE", decision.matched_rule_id)
        self.assertEqual("medium", decision.evidence_level)
        self.assertFalse(decision.resolved)
        self.assertTrue(decision.label_kept)

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
        # 重构后口径：evidence_level 由证据打分映射（55分=中），不再取规则常量 high
        self.assertEqual("medium", bank_interest.evidence_level)
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


    def test_label_conflict_with_insufficient_evidence_keeps_original(self) -> None:
        # 重构后口径：原标签与业务证据冲突但证据打分<70时，保留原标签送复核，业务项目仅作备选
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
        self.assertEqual("CFO-06", decision.system_item_id)
        self.assertEqual("LABEL-KEPT-INSUFFICIENT-EVIDENCE", decision.matched_rule_id)
        self.assertFalse(decision.resolved)
        self.assertTrue(decision.label_kept)
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

    def test_accounts_payable_alone_is_only_low_purchase_evidence(self) -> None:
        # 重构后口径：仅一级"应付账款"命中 15 分=低，不足以推翻任何原标签（服务费等已改归 CFO-07）
        # 关键词补强后"支付货款"已带直接证据（见下条测试），本测试摘要改用无业务线索的"支付款项"
        decision = classify_component(
            cashflow_component("支付款项", -100, ("应付账款_财务",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-04", decision.system_item_id)
        self.assertEqual("low", decision.evidence_level)
        self.assertEqual(15, decision.evidence_score)

    def test_pay_huokuan_summary_is_medium_purchase_evidence(self) -> None:
        # 新口径：摘要"支付货款"命中"付货款"（摘要分40），
        # 一级"应付账款"命中科目分15，项目总分=40+15=55分=中
        decision = classify_component(
            cashflow_component("支付货款", -100, ("应付账款_财务",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-04", decision.system_item_id)
        self.assertEqual("medium", decision.evidence_level)
        self.assertEqual(55, decision.evidence_score)

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
        # 新口径：摘要"偿还银行贷款利息"命中"贷款利息"40分 + 明细"利息支出"30分=70分=高（原标签CFF-05一致，仅作补充）
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
        # 重构后口径：单摘要词命中45分=中；预收退款为流出与CFO-01收入方向相反，得分减半=22分=低
        self.assertEqual("medium", receipt.evidence_level)
        self.assertEqual("CFO-01", refund.system_item_id)
        self.assertEqual("low", refund.evidence_level)

    def test_withheld_individual_income_tax_belongs_to_staff_payments(self) -> None:
        # 应用指南三(一)5：代扣代缴的个人所得税款应在"支付给职工以及为职工支付的现金"
        # 反映，不属于"支付的各项税费"
        decision = classify_component(
            cashflow_component("代扣个人所得税", -100, ("应交税费_应交个人所得税",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-05", decision.system_item_id)
        # 复核修复后口径：单摘要词40分=中（个税对方科目已被税费规则排除，项目归属未变）
        self.assertEqual(40, decision.evidence_score)
        self.assertEqual("medium", decision.evidence_level)

    def test_plain_individual_income_tax_payment_also_goes_to_staff_payments(self) -> None:
        # 复核修复：缴纳个人所得税同样随职工薪酬归 CFO-05（企业缴个税本质是代扣款的缴纳动作），
        # 个税对方科目已被 CFO-06-TAX/SOLE 的排除词拦截，不再落入"支付的各项税费"
        decision = classify_component(
            cashflow_component("缴纳个人所得税", -100, ("应交税费_应交个人所得税",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-05", decision.system_item_id)
        self.assertEqual("CFO-05-STAFF", decision.matched_rule_id)
        self.assertEqual("medium", decision.evidence_level)

    def test_enterprise_income_tax_still_uses_tax_payment_rule(self) -> None:
        # 回归保护：企业所得税仍命中 CFO-06 税费摘要词
        decision = classify_component(
            cashflow_component("缴纳企业所得税", -100, ("应交税费",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-06", decision.system_item_id)
        # 复核修复后口径：单摘要词40分=中
        self.assertEqual("medium", decision.evidence_level)

    def test_note_discounting_is_not_high_evidence_sales_collection(self) -> None:
        # 会计类第1号指引及上交所案例：贴现不符合终止确认条件时应列筹资流入并确认为借款，
        # 不得高证据归入销售回款；证据不足时落低证据兜底并进入复核
        decision = classify_component(
            cashflow_component("票据贴现", 100, ("应收票据_银行承兑汇票",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-03", decision.system_item_id)
        self.assertEqual("CFO-03-FALLBACK", decision.matched_rule_id)
        self.assertEqual("low", decision.evidence_level)

    def test_note_maturity_collection_stays_sales_collection(self) -> None:
        # 回归保护：票据到期收款仍是销售回款
        decision = classify_component(
            cashflow_component("票据到期收款", 100, ("应收票据",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-01", decision.system_item_id)
        # 复核修复后口径：摘要"票据到期收款"40分+对方科目一级"应收票据"15分=55分=中
        self.assertEqual("medium", decision.evidence_level)

    def test_wealth_management_redemption_principal_is_investment_recovery(self) -> None:
        # 应用指南三(二)1：理财/结构性存款赎回本金入"收回投资收到的现金"，
        # 只有收益部分才入"取得投资收益收到的现金"
        decision = classify_component(
            cashflow_component("赎回理财产品本金", 100, ("交易性金融资产",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-01", decision.system_item_id)
        # 重构后口径：摘要+对方科目一级两源印证55分=中
        self.assertEqual("medium", decision.evidence_level)

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
        # 重构后口径：单摘要词45分=中（项目归属未变）
        self.assertEqual("medium", decision.evidence_level)

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
        # 重构后口径：单"契税"摘要词40分=中
        self.assertEqual("medium", decision.evidence_level)

    def test_vehicle_purchase_tax_follows_asset_acquisition(self) -> None:
        # 车辆购置税为购置车辆的直接相关税费，随资产购建归 CFI-06
        decision = classify_component(
            cashflow_component("缴纳车辆购置税", -100, ("应交税费_应交车辆购置税",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFI-06", decision.system_item_id)
        # 重构后口径：单摘要词45分=中
        self.assertEqual("medium", decision.evidence_level)

    def test_returned_vat_credit_refund_is_tax_payment(self) -> None:
        # 应用指南：缴回留抵退税款属于"支付的各项税费"
        decision = classify_component(
            cashflow_component("缴回留抵退税款", -100, ("应交税费",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-06", decision.system_item_id)
        # 重构后口径：单摘要词50分=中（留抵退税缴回归属未变，仍为CFO-06）
        self.assertEqual("medium", decision.evidence_level)

    def test_received_vat_credit_refund_stays_tax_refund(self) -> None:
        # 回归保护：收到留抵退税款仍归"收到的税费返还"
        decision = classify_component(
            cashflow_component("收到留抵退税款", 100, ("应交税费",)),
            load_rule_pack(ROOT),
        )

        self.assertEqual("CFO-02", decision.system_item_id)
        # 重构后口径：单摘要词50分=中
        self.assertEqual("medium", decision.evidence_level)


def test_weak_single_account_evidence_keeps_original_label():
    """摘要指向工程的标签不被仅一级科目'应付账款'命中推翻（Task 3 电梯安装费回归案例）。"""
    from cashflow_direct.classification import classify_component, load_rule_pack

    rules = load_rule_pack(ROOT)
    component = CashflowComponent(
        component_id="CMP-LIFT", voucher_key="K", summary="付日立电梯公司电梯安装工程款",
        cash_delta_cent=-5000000, counterpart_accounts=("应付账款_应付设备款_暂估款",),
        original_item_text="购建固定资产、无形资产和其他长期资产支付的现金",
    )
    decision = classify_component(component, rules)
    assert decision.system_item_id == "CFI-06"
    assert decision.label_kept is True and decision.resolved is False
    assert decision.matched_rule_id == "LABEL-KEPT-INSUFFICIENT-EVIDENCE"
    assert decision.evidence_score < 70


def test_fee_type_split_categories():
    """Task 10 费用类保守分级拆分：明确复合词自动归位，裸服务费回落经营。"""
    from cashflow_direct.classification import classify_component, load_rule_pack

    rules = load_rule_pack(ROOT)
    cases = (
        ("审计费", ("管理费用",), "CFO-07"),
        ("生产外包服务费", ("生产成本",), "CFO-04"),
        ("工程监理费", ("在建工程",), "CFI-06"),
        ("融资顾问费", ("财务费用",), "CFF-06"),
        ("并购咨询费", ("管理费用",), "CFI-07"),
        ("支付咨询费", ("管理费用",), "CFO-07"),  # 裸词回落经营性兜底
        ("支付服务费", ("管理费用",), "CFO-07"),
    )
    for summary, counterparts, expected in cases:
        decision = classify_component(
            cashflow_component(summary, -100, counterparts), rules
        )
        assert decision.system_item_id == expected, f"{summary} -> {decision.system_item_id}"


def test_plain_vat_in_purchase_summary_is_not_tax_payment():
    """复核修复：裸"增值税"不再直接指向税费缴纳，采购摘要带"含增值税"归购买商品且不再冲突。"""
    decision = classify_component(
        cashflow_component("支付货款（含增值税）", -1130000, ("原材料_钢材",)),
        load_rule_pack(ROOT),
    )
    assert decision.system_item_id == "CFO-04"
    assert decision.resolved is True


def test_explicit_vat_payment_is_tax_payment():
    """回归保护："缴纳增值税"整词仍是 CFO-06 高证据（摘要40+对方科目明细30=70分=高）。"""
    decision = classify_component(
        cashflow_component("缴纳增值税", -340000, ("应交税费_未交增值税",)),
        load_rule_pack(ROOT),
    )
    assert decision.system_item_id == "CFO-06"
    assert decision.evidence_level == "high"


def test_staff_tax_for_construction_goes_to_cfi06():
    """复核修复：代扣个税+对方科目明细"资本化"经 CFI-06-STAFF-TAX 分流至购建固定资产（70分=高）。

    该分流规则要求必须命中对方科目才作数；与 CFO-05 的分差恰为一个明细档（30分），不算冲突。
    """
    decision = classify_component(
        cashflow_component("代扣个税", -50000, ("在建工程_资本化",)),
        load_rule_pack(ROOT),
    )
    assert decision.system_item_id == "CFI-06"
    assert decision.evidence_score == 70
    assert decision.resolved is True


def test_construction_wages_go_to_cfi06_with_conflict_flag():
    """复核修复："发放工资"+在建工程科目经 CFI-06-STAFF-COMP 得55分，胜过 CFO-05 的40分，

    但分差不足一个明细档（30分）计为冲突，首选 CFI-06 并按业务规则冲突口径送 AI 复核。
    """
    decision = classify_component(
        cashflow_component("发放工资", -200000, ("在建工程_厂房",)),
        load_rule_pack(ROOT),
    )
    assert decision.system_item_id == "CFI-06"
    assert decision.matched_rule_id == "BUSINESS-RULE-CONFLICT"
    assert decision.resolved is False


def test_fallback_reason_marks_internal_conservative_caliber():
    """复核修复：方向兜底理由必须声明此为内部保守处理口径，不是准则直接结论。"""
    decision = classify_component(
        cashflow_component("转账", -10000, ("其他科目_杂项",)),
        load_rule_pack(ROOT),
    )
    assert decision.matched_rule_id == "CFO-07-FALLBACK"
    assert "内部保守处理口径" in decision.reason


def test_dictionary_hit_reason_includes_note_id():
    """复核修复：经确认的公司特殊规则命中时，理由必须留 NOTE 编号痕迹。"""
    dictionary = AccountDictionary((
        AccountSemanticEntry("回购义务", "筹资", "CFF-06", "依据", "high", "custom", "NOTE-01"),
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
