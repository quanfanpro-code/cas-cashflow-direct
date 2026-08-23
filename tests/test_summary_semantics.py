from __future__ import annotations

import unittest
from pathlib import Path

from cashflow_direct.decision_policy import EvidenceQuality
from cashflow_direct.summary_semantics import (
    SummarySemanticResult,
    analyze_summary,
    build_summary_agent_task,
    load_summary_rules,
    merge_summary_agent_slots,
    validate_summary_batch,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class SummarySemanticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_summary_rules(PROJECT_ROOT)

    def test_noise_is_marked_without_changing_original_offsets(self):
        text = "2026年3月支付成都工程投资有限公司差旅费1000元"
        result = analyze_summary(text, self.rules)
        self.assertEqual(text, result.summary)
        self.assertTrue(all(text[span.start : span.end] == span.text for span in result.spans))
        self.assertNotIn("CFI-07", result.candidate_item_ids)

    def test_subject_action_relation_is_not_split_into_two_cash_legs(self):
        result = analyze_summary("收到客户支付货款", self.rules)
        self.assertEqual(("CFO-01",), result.candidate_item_ids)
        self.assertEqual(
            ["收到"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )

    def test_one_action_with_two_objects_stays_ambiguous(self):
        result = analyze_summary("支付货款及设备款", self.rules)
        self.assertEqual({"CFO-04", "CFI-06"}, set(result.candidate_item_ids))
        self.assertIs(EvidenceQuality.WEAK, result.quality)

    def test_entity_type_does_not_invent_business_role(self):
        result = analyze_summary("支付张三往来款", self.rules)
        self.assertFalse(
            any(span.slot in {"employee", "shareholder", "customer"} for span in result.spans)
        )

    def test_authoritative_quality_examples(self):
        self.assertEqual(10, analyze_summary("付款", self.rules).quality.value)
        self.assertEqual(10, analyze_summary("支付某公司咨询费", self.rules).quality.value)
        tax = analyze_summary("缴纳税款", self.rules)
        self.assertEqual(("CFO-06",), tax.candidate_item_ids)
        self.assertEqual(25, tax.quality.value)
        asset = analyze_summary("支付设备购置款用于生产线建设", self.rules)
        self.assertEqual(("CFI-06",), asset.candidate_item_ids)
        self.assertEqual(45, asset.quality.value)

    def test_wage_account_phrase_does_not_become_staff_compensation(self):
        for summary in (
            "付农民工资专用账户款",
            "转农民工工资专户资金",
            "支付工资保证金",
        ):
            with self.subTest(summary=summary):
                result = analyze_summary(summary, self.rules)
                self.assertNotIn("CFO-05", result.candidate_item_ids)
                self.assertLess(result.quality.value, 45)

    def test_employee_advance_return_does_not_become_financing_borrowing(self):
        for summary in (
            "收到员工退回借款",
            "收回职工借支",
            "收到员工备用金退回",
        ):
            with self.subTest(summary=summary):
                result = analyze_summary(summary, self.rules)
                self.assertNotIn("CFF-02", result.candidate_item_ids)
                self.assertIn("CFO-03", result.candidate_item_ids)

    def test_equipment_purchase_is_not_monopolized_by_goods_word(self):
        result = analyze_summary("支付设备采购货款", self.rules)
        self.assertNotEqual(("CFO-04",), result.candidate_item_ids)
        self.assertIn("CFI-06", result.candidate_item_ids)

    def test_individual_tax_needs_an_employee_service_object(self):
        for summary in (
            "缴纳分红个税",
            "代缴股权转让个人所得税",
            "缴纳个人所得税",
        ):
            with self.subTest(summary=summary):
                result = analyze_summary(summary, self.rules)
                self.assertFalse(
                    result.candidate_item_ids == ("CFO-05",)
                    and result.quality is EvidenceQuality.STRONG
                )

        employee = analyze_summary("支付本公司员工本月工资薪金", self.rules)
        self.assertEqual(("CFO-05",), employee.candidate_item_ids)
        self.assertIs(EvidenceQuality.STRONG, employee.quality)

    def test_completed_refund_is_an_ordinary_semantic_relation(self):
        result = analyze_summary("收到员工退回备用金", self.rules)
        self.assertTrue(any(span.slot == "refund" for span in result.spans))
        self.assertIn(result.quality.value, {25, 45})

    def test_conditional_refund_is_capped_at_weak(self):
        self.assertLessEqual(analyze_summary("其中部分以后原路返回", self.rules).quality.value, 10)

    def test_invoice_context_does_not_invent_cash_action(self):
        result = analyze_summary("电费发票进项", self.rules)
        self.assertFalse(any(span.slot == "cash_action" for span in result.spans))
        self.assertEqual((), result.candidate_item_ids)

    def test_long_object_phrase_does_not_swallow_its_cash_action(self):
        result = analyze_summary("偿还银行借款本金", self.rules)

        self.assertEqual(("CFF-04",), result.candidate_item_ids)
        self.assertEqual(45, result.quality.value)
        self.assertIn("cash_action", {span.slot for span in result.spans})
        self.assertIn("business_object", {span.slot for span in result.spans})

    def test_agent_only_receives_unresolved_language_slots(self):
        resolved = analyze_summary("缴纳税款", self.rules)
        self.assertIsNone(build_summary_agent_task(resolved))
        unresolved = analyze_summary("代甲方向乙方转处理尾款", self.rules)
        task = build_summary_agent_task(unresolved)
        self.assertIsNotNone(task)
        self.assertEqual(
            {"task_id", "summary", "unresolved_slots", "allowed_slots", "instruction"},
            set(task or {}),
        )

    def test_organization_name_does_not_create_a_second_cash_action(self):
        result = analyze_summary("收到支付宝支付科技有限公司款，验资", self.rules)

        self.assertEqual("rule_complete", result.status)
        self.assertEqual(
            ["收到"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )

    def test_refund_relation_after_receipt_is_not_a_second_cash_leg(self):
        result = analyze_summary("收到周杰退回备用金", self.rules)

        self.assertEqual("rule_complete", result.status)
        self.assertEqual(
            ["收到"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )
        self.assertTrue(any(span.slot == "refund" for span in result.spans))

    def test_purpose_wording_does_not_create_a_second_cash_leg(self):
        result = analyze_summary(
            "6.6支付陈思韬报5.29-5.30出差资阳沟通回款挂账住宿费",
            self.rules,
        )

        self.assertEqual("rule_complete", result.status)
        self.assertEqual(
            ["支付"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )

    def test_account_label_and_noun_do_not_create_cash_actions(self):
        result = analyze_summary(
            "收到资金集中收付管理中心第十六支出户款，项目回款",
            self.rules,
        )
        compensation = analyze_summary("收到虎文辉赔付款", self.rules)

        self.assertEqual("rule_complete", result.status)
        self.assertEqual("rule_complete", compensation.status)
        self.assertEqual(
            ["收到", "回款"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )
        self.assertEqual(
            ["收到"],
            [span.text for span in compensation.spans if span.slot == "cash_action"],
        )

    def test_current_payment_is_separated_from_history_and_future_terms(self):
        result = analyze_summary(
            "付设备验收款，合同前期已支付30%，尾款一年后支付",
            self.rules,
        )

        self.assertEqual("rule_complete", result.status)
        self.assertEqual(
            ["付"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )

    def test_travel_purpose_collection_is_not_a_second_cash_leg(self):
        result = analyze_summary(
            "付任映燃报销出差包头，核对账务，跟进项目合同及四季度回款事宜住宿",
            self.rules,
        )

        self.assertEqual("rule_complete", result.status)
        self.assertEqual(
            ["付"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )

    def test_receiving_an_invoice_is_document_context_not_cash_inflow(self):
        result = analyze_summary("付报销款，收到火车票发票", self.rules)
        missing_invoice = analyze_summary("付单位电话费，发票暂未取得", self.rules)

        self.assertEqual("rule_complete", result.status)
        self.assertEqual("rule_complete", missing_invoice.status)
        self.assertEqual(
            ["付"],
            [span.text for span in result.spans if span.slot == "cash_action"],
        )
        self.assertEqual(
            ["付"],
            [span.text for span in missing_invoice.spans if span.slot == "cash_action"],
        )

    def test_entertainment_expense_does_not_create_a_conditional_marker(self):
        result = analyze_summary("付出差招待费", self.rules)

        self.assertFalse(any(span.slot == "conditional" for span in result.spans))

    def test_penalty_candidate_respects_the_cash_action(self):
        received = analyze_summary("收到客户罚款", self.rules)
        paid = analyze_summary("支付税收滞纳金", self.rules)
        action_missing = analyze_summary("罚款", self.rules)

        self.assertEqual(("CFO-03",), received.candidate_item_ids)
        self.assertEqual(("CFO-07",), paid.candidate_item_ids)
        self.assertEqual(("CFO-03", "CFO-07"), action_missing.candidate_item_ids)
        self.assertEqual(EvidenceQuality.WEAK, action_missing.quality)

    def test_agent_cannot_return_accounting_decision_fields(self):
        unresolved = analyze_summary("代甲方向乙方转处理尾款", self.rules)
        with self.assertRaisesRegex(ValueError, "不得返回"):
            merge_summary_agent_slots(
                unresolved,
                {"item_id": "CFO-07", "spans": []},
                self.rules,
            )

    def test_batch_rejects_degenerate_results(self):
        empty = SummarySemanticResult(
            "摘要甲",
            "rule_complete",
            (),
            (),
            EvidenceQuality.INVALID,
            "占位",
        )
        with self.assertRaisesRegex(ValueError, "整批摘要语义退化"):
            validate_summary_batch((empty,), ("摘要甲",))

    def test_rule_file_covers_all_statement_leaf_items(self):
        covered = {
            item_id
            for rule in self.rules["candidate_rules"]
            for item_id in rule["candidate_item_ids"]
        }
        self.assertEqual(
            {
                "CFO-01", "CFO-02", "CFO-03", "CFO-04", "CFO-05", "CFO-06", "CFO-07",
                "CFI-01", "CFI-02", "CFI-03", "CFI-04", "CFI-05", "CFI-06", "CFI-07",
                "CFI-08", "CFI-09", "CFF-01", "CFF-02", "CFF-03", "CFF-04", "CFF-05",
                "CFF-06",
            },
            covered,
        )


if __name__ == "__main__":
    unittest.main()
