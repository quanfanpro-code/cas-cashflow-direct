from __future__ import annotations

import unittest

from cashflow_direct.components import (
    _signed_flow,
    build_cashflow_components,
    confirm_cash_scope,
    discover_cash_scope,
    flow_direction_source,
)
from tests.fixture_factory import _component_entry, component_entries


def _confirmed_scope(case: str):
    proposal = discover_cash_scope(component_entries(case))
    return confirm_cash_scope(
        proposal,
        {candidate.account_key: "include" for candidate in proposal.candidates},
    )


class ComponentTests(unittest.TestCase):
    def test_internal_transfer_is_excluded_but_external_receipt_in_same_voucher_survives(self) -> None:
        entries = component_entries("internal_and_external")
        result = build_cashflow_components(entries, _confirmed_scope("internal_and_external"))
        self.assertEqual(1, len(result.components))
        self.assertEqual(30_000, result.components[0].cash_delta_cent)
        self.assertEqual(2, len(result.excluded_internal_transfers))

    def test_multi_project_voucher_splits_to_exact_cash_total(self) -> None:
        entries = component_entries("multi_project_receipt")
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})
        result = build_cashflow_components(entries, scope)
        self.assertEqual(9_052_530, sum(item.cash_delta_cent for item in result.components))
        self.assertEqual(2, len({item.original_item_text for item in result.components}))

    def test_pure_internal_transfer_has_no_statement_component(self) -> None:
        result = build_cashflow_components(
            component_entries("pure_internal"), _confirmed_scope("pure_internal")
        )
        self.assertEqual((), result.components)
        self.assertEqual(2, len(result.excluded_internal_transfers))

    def test_same_voucher_receipt_and_fee_keep_both_real_directions(self) -> None:
        result = build_cashflow_components(
            component_entries("receipt_and_fee"), _confirmed_scope("receipt_and_fee")
        )
        self.assertEqual([1_000_000, -500], sorted(
            (item.cash_delta_cent for item in result.components), reverse=True
        ))
        self.assertEqual((), result.excluded_internal_transfers)

    def test_explicit_cash_counterpart_is_internal_transfer(self) -> None:
        result = build_cashflow_components(
            component_entries("explicit_internal_transfer"),
            _confirmed_scope("explicit_internal_transfer"),
        )
        self.assertEqual((), result.components)
        self.assertEqual(2, len(result.excluded_internal_transfers))

    def test_unlabeled_principal_and_interest_keep_separate_counterparts(self) -> None:
        result = build_cashflow_components(
            component_entries("principal_and_interest"),
            _confirmed_scope("principal_and_interest"),
        )
        self.assertEqual([-100_000, -10_000], sorted(
            (item.cash_delta_cent for item in result.components)
        ))
        self.assertEqual(
            {("短期借款",), ("应付利息",)},
            {item.counterpart_accounts for item in result.components},
        )

    def test_unlabeled_purchase_and_input_vat_stay_in_one_cash_business(self) -> None:
        result = build_cashflow_components(
            component_entries("purchase_with_input_vat"),
            _confirmed_scope("purchase_with_input_vat"),
        )
        self.assertEqual(1, len(result.components))
        self.assertEqual(-113_000, result.components[0].cash_delta_cent)
        self.assertEqual(
            ("原材料", "应交税费-应交增值税（进项税额）"),
            result.components[0].counterpart_accounts,
        )

    def test_one_sided_cash_leg_with_confirmed_cash_counterpart_is_internal(self) -> None:
        result = build_cashflow_components(
            component_entries("one_sided_internal_transfer"),
            _confirmed_scope("one_sided_internal_transfer"),
        )
        self.assertEqual((), result.components)
        self.assertEqual(1, len(result.excluded_internal_transfers))

    def test_one_sided_evidence_does_not_invent_counterpart(self) -> None:
        counterpart = build_cashflow_components(
            component_entries("one_sided_counterpart"), _confirmed_scope("one_sided_counterpart")
        ).components[0]
        cash = build_cashflow_components(
            component_entries("one_sided_cash"), _confirmed_scope("one_sided_cash")
        ).components[0]
        summary_only = build_cashflow_components(
            component_entries("summary_only"), _confirmed_scope("summary_only")
        ).components[0]
        self.assertEqual(("应收款项",), counterpart.counterpart_accounts)
        self.assertEqual((), cash.counterpart_accounts)
        self.assertEqual((), summary_only.counterpart_accounts)

    def test_counterpart_side_ledger_without_flow_amount_uses_named_cash_counterpart(self) -> None:
        entries = component_entries("one_sided_counterpart_without_flow_amount")
        proposal = discover_cash_scope(entries)
        self.assertEqual(("1002",), tuple(item.account_key for item in proposal.candidates))
        result = build_cashflow_components(
            entries,
            confirm_cash_scope(proposal, {"1002": "include"}),
        )
        self.assertEqual(1, len(result.components))
        self.assertEqual(12_000, result.components[0].cash_delta_cent)
        self.assertEqual(("应收款项",), result.components[0].counterpart_accounts)

    def test_summary_only_uses_flow_item_direction_when_retained_side_is_unknown(self) -> None:
        result = build_cashflow_components(
            component_entries("summary_only_counterpart_direction"),
            _confirmed_scope("summary_only_counterpart_direction"),
        )
        self.assertEqual(-16_000, result.components[0].cash_delta_cent)

    def test_flow_amount_has_priority_and_split_label_is_used_once(self) -> None:
        flow = build_cashflow_components(
            component_entries("flow_amount_differs"), _confirmed_scope("flow_amount_differs")
        )
        split = build_cashflow_components(
            component_entries("split_label_duplication"), _confirmed_scope("split_label_duplication")
        )
        self.assertEqual(18_000, flow.components[0].cash_delta_cent)
        self.assertEqual(1, len(split.components))
        self.assertEqual(25_000, split.components[0].cash_delta_cent)
        self.assertEqual(len(split.components[0].source_keys), len(set(split.components[0].source_keys)))

    def test_unbalanced_voucher_keeps_determinable_cash_and_marks_anomaly(self) -> None:
        result = build_cashflow_components(
            component_entries("unbalanced_cash_fact"), _confirmed_scope("unbalanced_cash_fact")
        )
        self.assertEqual(30_000, result.components[0].cash_delta_cent)
        self.assertIn("voucher_unbalanced", result.components[0].anomalies)

    def test_counterpart_side_flow_detail_uses_item_direction(self) -> None:
        # 清平式：对方科目侧 + 借贷列 + 流量金额 + 标准项目名 → 方向听项目名
        entries = (
            _component_entry(1, "V1", "销售费用_业务招待费", debit_cent=600_00, item="支付其他与经营活动有关的现金", flow_amount_cent=600_00, retained_side="counterpart"),
            _component_entry(2, "V2", "合同负债_业务", credit_cent=700000_00, item="销售商品、提供劳务收到的现金", flow_amount_cent=700000_00, retained_side="counterpart"),
        )
        self.assertEqual(-600_00, _signed_flow(entries[0]))
        self.assertEqual(700000_00, _signed_flow(entries[1]))

    def test_red_reversal_sign_is_preserved(self) -> None:
        # 负流量+流入项目 → 负（退款冲减流入）；负流量+流出项目 → 正（购买退款冲减流出）
        refund_in = _component_entry(3, "V3", "其他应收款_其他", debit_cent=-180_00, item="收到其他与经营活动有关的现金", flow_amount_cent=-180_00, retained_side="counterpart")
        refund_out = _component_entry(4, "V4", "应付账款_财务", credit_cent=29000_00, item="支付其他与经营活动有关的现金", flow_amount_cent=-29000_00, retained_side="counterpart")
        self.assertEqual(-180_00, _signed_flow(refund_in))
        self.assertEqual(29000_00, _signed_flow(refund_out))

    def test_unknown_item_name_falls_back_to_legacy(self) -> None:
        # 非标项目名（项目甲）不进新分支，行为与改动前一致
        entry = _component_entry(5, "V5", "应收款项", credit_cent=12000_00, item="项目甲", flow_amount_cent=12000_00, retained_side="counterpart")
        self.assertEqual(12000_00, _signed_flow(entry))

    def test_flow_direction_source_labels_all_four_cases(self) -> None:
        inflow = _component_entry(10, "V10", "银行存款", debit_cent=100_00, item="销售商品收到的现金", flow_amount_cent=100_00, retained_side="cash")
        outflow = _component_entry(11, "V11", "银行存款", credit_cent=100_00, item="购买商品支付的现金", flow_amount_cent=100_00, retained_side="cash")
        debit_credit = _component_entry(12, "V12", "银行存款", debit_cent=100_00, flow_amount_cent=100_00, retained_side="cash")
        balance = _component_entry(13, "V13", "银行存款", debit_cent=100_00, retained_side="cash")
        self.assertEqual("现流项目名(流入)", flow_direction_source(inflow))
        self.assertEqual("现流项目名(流出)", flow_direction_source(outflow))
        self.assertEqual("借贷列+流量金额", flow_direction_source(debit_credit))
        self.assertEqual("借贷差额", flow_direction_source(balance))

    def test_every_cash_candidate_requires_one_confirmation(self) -> None:
        proposal = discover_cash_scope(component_entries("pure_internal"))
        with self.assertRaisesRegex(ValueError, "等待现金范围确认"):
            confirm_cash_scope(proposal, {})


    def test_accrual_summary_is_marked_non_cash_and_excluded(self) -> None:
        # 摘要写"计提"的凭证 → non_cash anomaly → 分类 EXCLUDED
        from pathlib import Path
        from cashflow_direct.classification import classify_component, load_rule_pack

        entries = (
            _component_entry(1, "V1", "1002 银行存款", credit_cent=100_000, retained_side="cash", summary="计提坏账准备"),
            _component_entry(2, "V1", "信用减值损失", debit_cent=100_000, retained_side="counterpart", summary="计提坏账准备"),
        )
        proposal = discover_cash_scope(entries)
        scope = confirm_cash_scope(
            proposal, {candidate.account_key: "include" for candidate in proposal.candidates}
        )
        result = build_cashflow_components(entries, scope)
        self.assertEqual(1, len(result.components))
        self.assertIn("non_cash", result.components[0].anomalies)
        rules = load_rule_pack(Path(__file__).resolve().parents[1])
        self.assertTrue(classify_component(result.components[0], rules).excluded)

    def test_note_endorsement_settlement_without_cash_is_non_cash(self) -> None:
        # 应收票据背书抵应付账款（无货币资金）→ non_cash anomaly
        from pathlib import Path
        from cashflow_direct.classification import classify_component, load_rule_pack

        entries = (
            _component_entry(1, "V2", "1121 应收票据", credit_cent=58_972_968_30, retained_side="counterpart", flow_amount_cent=-58_972_968_30, summary="票据背书抵应付账款"),
            _component_entry(2, "V2", "2202 应付账款", debit_cent=58_972_968_30, retained_side="counterpart", flow_amount_cent=58_972_968_30, summary="票据背书抵应付账款"),
        )
        proposal = discover_cash_scope(entries)
        scope = confirm_cash_scope(
            proposal, {candidate.account_key: "include" for candidate in proposal.candidates}
        )
        result = build_cashflow_components(entries, scope)
        self.assertTrue(result.components)
        self.assertTrue(all("non_cash" in component.anomalies for component in result.components))
        rules = load_rule_pack(Path(__file__).resolve().parents[1])
        self.assertTrue(all(classify_component(component, rules).excluded for component in result.components))

    def test_note_receipt_with_cash_counterpart_stays_real_cash(self) -> None:
        # 票据贴现/到期收款：应收票据 + 银行存款对方科目 → 真实现金流，不得误标 non_cash
        from pathlib import Path
        from cashflow_direct.classification import classify_component, load_rule_pack

        entries = (
            _component_entry(
                1, "V3", "1121 应收票据_银行承兑汇票",
                credit_cent=1_000_000, flow_amount_cent=1_000_000,
                retained_side="counterpart", counterpart_name="1002 银行存款",
                summary="票据贴现收款",
            ),
        )
        proposal = discover_cash_scope(entries)
        scope = confirm_cash_scope(
            proposal, {candidate.account_key: "include" for candidate in proposal.candidates}
        )
        result = build_cashflow_components(entries, scope)
        self.assertEqual(1, len(result.components))
        self.assertNotIn("non_cash", result.components[0].anomalies)
        rules = load_rule_pack(Path(__file__).resolve().parents[1])
        self.assertFalse(classify_component(result.components[0], rules).excluded)

    def test_note_only_voucher_with_flow_label_is_not_auto_non_cash(self) -> None:
        # 真实客户形态：票据贴现/到期收款凭证只有应收票据行、对方科目为空、带现流项目标签，
        # 没有票据腿与往来腿互抵的证据，不得自动标 non_cash（保守：宁可进规则与 AI 复核）
        from pathlib import Path
        from cashflow_direct.classification import classify_component, load_rule_pack

        entries = (
            _component_entry(
                1, "V4", "1121 应收票据_银行承兑汇票",
                credit_cent=2_038_000_00, flow_amount_cent=2_038_000_00,
                retained_side="counterpart", item="销售商品、提供劳务收到的现金",
                summary="票据贴现",
            ),
        )
        proposal = discover_cash_scope(entries)
        scope = confirm_cash_scope(proposal, {})
        result = build_cashflow_components(entries, scope)
        self.assertEqual(1, len(result.components))
        self.assertNotIn("non_cash", result.components[0].anomalies)
        rules = load_rule_pack(Path(__file__).resolve().parents[1])
        self.assertFalse(classify_component(result.components[0], rules).excluded)

if __name__ == "__main__":
    unittest.main()
