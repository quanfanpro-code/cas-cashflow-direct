from __future__ import annotations

import unittest
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import pytest

from cashflow_direct.account_dictionary import load_common_dictionary
from cashflow_direct.classification import classify_component, load_rule_pack
from cashflow_direct.components import (
    _signed_flow,
    _minimum_amount_row_combinations,
    _unique_minimum_amount_rows,
    build_cashflow_components,
    confirm_cash_scope,
    discover_cash_scope,
    find_cash_row_cleanup_requests,
    flow_direction_source,
)
from cashflow_direct.summary_semantics import analyze_summary, load_summary_rules
from tests.fixture_factory import _component_entry, component_entries


ROOT = Path(__file__).resolve().parents[1]


def _classify(component):
    return classify_component(
        component,
        load_rule_pack(ROOT),
        load_common_dictionary(ROOT),
        {component.summary: analyze_summary(component.summary, load_summary_rules(ROOT))},
    )


def _confirmed_scope(case: str):
    proposal = discover_cash_scope(component_entries(case))
    return confirm_cash_scope(
        proposal,
        {candidate.account_key: "include" for candidate in proposal.candidates},
    )


class ComponentTests(unittest.TestCase):
    def test_excessive_amount_combination_states_fall_back_to_ambiguous(self) -> None:
        rows = tuple(
            _component_entry(
                900 + index,
                "V-MANY-AMOUNTS",
                f"应付账款_{index}",
                debit_cent=2**index,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary=f"第{index}项业务",
            )
            for index in range(18)
        )

        self.assertEqual(
            (),
            _unique_minimum_amount_rows(
                rows, -sum(2**index for index in range(18))
            ),
        )

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

        allocated_by_entry: dict[str, int] = defaultdict(int)
        allocated_by_component: dict[str, int] = defaultdict(int)
        for allocation in result.source_allocations:
            allocated_by_entry[allocation.entry_id] += allocation.allocated_cent
            allocated_by_component[allocation.component_id] += allocation.allocated_cent
        self.assertEqual(9_052_530, allocated_by_entry["E6"])
        self.assertEqual(
            {item.component_id: item.cash_delta_cent for item in result.components},
            dict(allocated_by_component),
        )

    def test_same_item_business_rows_in_one_voucher_stay_separate(self) -> None:
        entries = (
            _component_entry(
                20,
                "V-MULTI-BUSINESS",
                "1002 银行存款",
                credit_cent=10_000,
                retained_side="cash",
                summary="支付两项采购款",
            ),
            _component_entry(
                21,
                "V-MULTI-BUSINESS",
                "应付账款_材料供应商",
                debit_cent=6_000,
                item="购买商品、接受劳务支付的现金",
                flow_amount_cent=6_000,
                retained_side="counterpart",
                summary="支付材料款",
            ),
            _component_entry(
                22,
                "V-MULTI-BUSINESS",
                "应付账款_设备供应商",
                debit_cent=4_000,
                item="购买商品、接受劳务支付的现金",
                flow_amount_cent=4_000,
                retained_side="counterpart",
                summary="支付设备款",
            ),
            _component_entry(
                23,
                "V-MULTI-BUSINESS",
                "应交税费_进项税额",
                debit_cent=1_300,
                retained_side="counterpart",
                summary="暂估税额冲回",
            ),
            _component_entry(
                24,
                "V-MULTI-BUSINESS",
                "应付账款_暂估",
                credit_cent=1_300,
                retained_side="counterpart",
                summary="暂估税额冲回",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        result = build_cashflow_components(entries, scope)

        self.assertEqual(2, len(result.components))
        self.assertEqual(
            {
                (-6_000, "支付材料款", ("应付账款_材料供应商",)),
                (-4_000, "支付设备款", ("应付账款_设备供应商",)),
            },
            {
                (item.cash_delta_cent, item.summary, item.counterpart_accounts)
                for item in result.components
            },
        )
        self.assertEqual(
            -10_000,
            sum(item.allocated_cent for item in result.source_allocations),
        )

    def test_two_cash_rows_in_one_voucher_keep_separate_component_identity(self) -> None:
        entries = (
            _component_entry(
                200,
                "V-TWO-CASH-ROWS",
                "1002 银行存款_基本户",
                credit_cent=6_000,
                retained_side="cash",
                summary="支付材料款",
            ),
            _component_entry(
                201,
                "V-TWO-CASH-ROWS",
                "1002 银行存款_一般户",
                credit_cent=4_000,
                retained_side="cash",
                summary="支付设备款",
            ),
            _component_entry(
                202,
                "V-TWO-CASH-ROWS",
                "原材料_甲材料",
                debit_cent=6_000,
                retained_side="counterpart",
                summary="支付材料款",
            ),
            _component_entry(
                203,
                "V-TWO-CASH-ROWS",
                "固定资产_生产设备",
                debit_cent=4_000,
                retained_side="counterpart",
                summary="支付设备款",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        result = build_cashflow_components(entries, scope)

        self.assertEqual(2, len(result.components))
        self.assertEqual(
            {
                (-6_000, "支付材料款", ("原材料_甲材料",)),
                (-4_000, "支付设备款", ("固定资产_生产设备",)),
            },
            {
                (item.cash_delta_cent, item.summary, item.counterpart_accounts)
                for item in result.components
            },
        )
        cash_entry_ids = {"E200", "E201"}
        self.assertEqual(
            [1, 1],
            sorted(
                len(cash_entry_ids & set(item.source_keys))
                for item in result.components
            ),
        )

    def test_single_labeled_business_does_not_absorb_unrelated_voucher_rows(self) -> None:
        entries = (
            _component_entry(
                30,
                "V-ONE-BUSINESS",
                "1002 银行存款",
                credit_cent=10_000,
                retained_side="cash",
                summary="支付货款",
            ),
            _component_entry(
                31,
                "V-ONE-BUSINESS",
                "应付账款_结算",
                debit_cent=10_000,
                item="购买商品、接受劳务支付的现金",
                flow_amount_cent=10_000,
                retained_side="counterpart",
                summary="支付货款",
            ),
            _component_entry(
                32,
                "V-ONE-BUSINESS",
                "应交税费_进项税额",
                debit_cent=1_300,
                retained_side="counterpart",
                summary="暂估税额冲回",
            ),
            _component_entry(
                33,
                "V-ONE-BUSINESS",
                "应付账款_暂估",
                credit_cent=1_300,
                retained_side="counterpart",
                summary="暂估税额冲回",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        component = build_cashflow_components(entries, scope).components[0]

        self.assertEqual("支付货款", component.summary)
        self.assertEqual(("应付账款_结算",), component.counterpart_accounts)
        self.assertNotIn("E32", component.source_keys)
        self.assertNotIn("E33", component.source_keys)

    def test_cash_payment_uses_unique_same_summary_amount_match_and_ignores_accrual_rows(self) -> None:
        entries = (
            _component_entry(34, "V-PAYROLL-MIXED", "1002 银行存款", credit_cent=18_567_466, retained_side="cash", summary="发放7月工资"),
            _component_entry(35, "V-PAYROLL-MIXED", "应付职工薪酬_工资", debit_cent=18_567_466, retained_side="counterpart", summary="发放7月工资"),
            _component_entry(36, "V-PAYROLL-MIXED", "应付职工薪酬_工资", debit_cent=20_000, retained_side="counterpart", summary="补计提7月工资"),
            _component_entry(37, "V-PAYROLL-MIXED", "管理费用_工资", credit_cent=20_000, retained_side="counterpart", summary="补计提7月工资"),
            _component_entry(38, "V-PAYROLL-MIXED", "应交税费_应交个人所得税", debit_cent=213_477, retained_side="counterpart", summary="结转个人所得税"),
            _component_entry(39, "V-PAYROLL-MIXED", "应付职工薪酬_工资", credit_cent=213_477, retained_side="counterpart", summary="结转个人所得税"),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        component = build_cashflow_components(entries, scope).components[0]

        self.assertEqual(-18_567_466, component.cash_delta_cent)
        self.assertEqual("发放7月工资", component.summary)
        self.assertEqual(("应付职工薪酬_工资",), component.counterpart_accounts)
        self.assertEqual({"E34", "E35"}, set(component.source_keys))

    def test_component_uses_the_connected_business_row_summary(self) -> None:
        entries = (
            _component_entry(
                60,
                "V-SUMMARY",
                "1002 银行存款",
                credit_cent=10_000,
                retained_side="cash",
                summary="凭证第一行总述",
            ),
            _component_entry(
                61,
                "V-SUMMARY",
                "应付职工薪酬_工资",
                debit_cent=10_000,
                item="支付给职工以及为职工支付的现金",
                retained_side="counterpart",
                summary="发放工资",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        component = build_cashflow_components(entries, scope).components[0]

        self.assertEqual("发放工资", component.summary)

    def test_same_item_rows_split_by_balancing_amount_without_flow_amount_column(self) -> None:
        entries = (
            _component_entry(
                70,
                "V-AMBIGUOUS",
                "1002 银行存款",
                credit_cent=10_000,
                retained_side="cash",
                summary="凭证总述",
            ),
            _component_entry(
                71,
                "V-AMBIGUOUS",
                "应付账款_甲",
                debit_cent=6_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付材料款",
            ),
            _component_entry(
                72,
                "V-AMBIGUOUS",
                "应付账款_乙",
                debit_cent=4_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付设备款",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        result = build_cashflow_components(entries, scope)

        self.assertEqual(
            {
                (-6_000, "支付材料款", ("应付账款_甲",)),
                (-4_000, "支付设备款", ("应付账款_乙",)),
            },
            {
                (item.cash_delta_cent, item.summary, item.counterpart_accounts)
                for item in result.components
            },
        )
        self.assertEqual(
            -10_000,
            sum(item.allocated_cent for item in result.source_allocations),
        )

    def test_balancing_labeled_adjustments_do_not_inflate_cashflow_gross_amounts(self) -> None:
        entries = (
            _component_entry(
                73,
                "V-LABELED-ADJUSTMENTS",
                "1002 银行存款",
                credit_cent=10_000,
                retained_side="cash",
                summary="支付两项采购款",
            ),
            _component_entry(
                74,
                "V-LABELED-ADJUSTMENTS",
                "应付账款_材料",
                debit_cent=6_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付材料款",
            ),
            _component_entry(
                75,
                "V-LABELED-ADJUSTMENTS",
                "应付账款_设备",
                debit_cent=4_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付设备款",
            ),
            _component_entry(
                76,
                "V-LABELED-ADJUSTMENTS",
                "应付账款_调整借方",
                debit_cent=3_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="非现金调整",
            ),
            _component_entry(
                77,
                "V-LABELED-ADJUSTMENTS",
                "应付账款_调整贷方",
                credit_cent=3_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="非现金调整",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        result = build_cashflow_components(entries, scope)

        self.assertEqual(
            {(-6_000, "支付材料款"), (-4_000, "支付设备款")},
            {(item.cash_delta_cent, item.summary) for item in result.components},
        )
        used_keys = {key for item in result.components for key in item.source_keys}
        self.assertNotIn("E76", used_keys)
        self.assertNotIn("E77", used_keys)
        self.assertEqual(-10_000, sum(item.cash_delta_cent for item in result.components))

    def test_non_unique_minimum_amount_combinations_stay_ambiguous(self) -> None:
        entries = (
            _component_entry(
                78,
                "V-NON-UNIQUE",
                "1002 银行存款",
                credit_cent=10_000,
                retained_side="cash",
                summary="支付采购款",
            ),
            _component_entry(
                79,
                "V-NON-UNIQUE",
                "应付账款_甲",
                debit_cent=6_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付甲材料款",
            ),
            _component_entry(
                80,
                "V-NON-UNIQUE",
                "应付账款_乙",
                debit_cent=6_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付乙材料款",
            ),
            _component_entry(
                81,
                "V-NON-UNIQUE",
                "应付账款_共同",
                debit_cent=4_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付共同材料款",
            ),
            _component_entry(
                82,
                "V-NON-UNIQUE",
                "应付账款_非现金冲回",
                credit_cent=6_000,
                retained_side="counterpart",
                summary="非现金冲回",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        result = build_cashflow_components(entries, scope)

        self.assertEqual(1, len(result.components))
        self.assertEqual(-10_000, result.components[0].cash_delta_cent)
        self.assertEqual("", result.components[0].summary)
        self.assertIn("summary_allocation_ambiguous", result.components[0].anomalies)
        self.assertEqual(1, len(result.structure_requests))
        self.assertEqual(
            {("E79", "E81"), ("E80", "E81")},
            set(result.structure_requests[0].candidate_entry_id_combinations),
        )

    def test_confirmed_component_structure_selection_builds_only_the_selected_rows(self) -> None:
        entries = (
            _component_entry(
                178,
                "V-SELECT-STRUCTURE",
                "1002 银行存款",
                credit_cent=10_000,
                retained_side="cash",
                summary="支付采购款",
            ),
            _component_entry(
                179,
                "V-SELECT-STRUCTURE",
                "应付账款_甲",
                debit_cent=6_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付甲材料款",
            ),
            _component_entry(
                180,
                "V-SELECT-STRUCTURE",
                "应付账款_乙",
                debit_cent=6_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付乙材料款",
            ),
            _component_entry(
                181,
                "V-SELECT-STRUCTURE",
                "应付账款_共同",
                debit_cent=4_000,
                item="购买商品、接受劳务支付的现金",
                retained_side="counterpart",
                summary="支付共同材料款",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        result = build_cashflow_components(
            entries,
            scope,
            structure_selections={"V-SELECT-STRUCTURE": ("E179", "E181")},
        )

        assert result.structure_requests == ()
        assert all(
            "path_depends_on_summary" in item.anomalies
            for item in result.components
        )
        assert {(item.cash_delta_cent, item.summary) for item in result.components} == {
            (-6_000, "支付甲材料款"),
            (-4_000, "支付共同材料款"),
        }

        independent = build_cashflow_components(
            entries,
            scope,
            structure_selections={"V-SELECT-STRUCTURE": ("E179", "E181")},
            structure_selection_basis={
                "V-SELECT-STRUCTURE": "independent_external"
            },
        )
        assert all(
            "path_depends_on_summary" not in item.anomalies
            for item in independent.components
        )

    def test_illegal_summary_keeps_the_cash_amount_and_marks_the_component(self) -> None:
        entry = replace(
            _component_entry(
                80,
                "V-ILLEGAL",
                "应付账款_甲",
                debit_cent=10_000,
                item="购买商品、接受劳务支付的现金",
                flow_amount_cent=10_000,
                retained_side="counterpart",
                counterpart_name="1002 银行存款",
                summary="",
            ),
            input_issues=("summary_empty",),
        )
        scope = confirm_cash_scope(discover_cash_scope((entry,)), {"1002": "include"})

        component = build_cashflow_components((entry,), scope).components[0]

        self.assertEqual(-10_000, component.cash_delta_cent)
        self.assertIn("summary_empty", component.anomalies)

    def test_pure_internal_transfer_has_no_statement_component(self) -> None:
        result = build_cashflow_components(
            component_entries("pure_internal"), _confirmed_scope("pure_internal")
        )
        self.assertEqual((), result.components)
        self.assertEqual(2, len(result.excluded_internal_transfers))

    def test_balanced_internal_transfer_ignores_a_misplaced_cashflow_label(self) -> None:
        entries = (
            _component_entry(
                81,
                "V-INTERNAL-LABEL",
                "1002 银行存款甲",
                debit_cent=27_002,
                retained_side="cash",
                summary="现金账户内部转入",
            ),
            _component_entry(
                82,
                "V-INTERNAL-LABEL",
                "1012 其他货币资金",
                credit_cent=27_002,
                item="收到其他与经营活动有关的现金",
                flow_amount_cent=27_002,
                retained_side="cash",
                summary="现金账户内部转出",
            ),
        )
        scope = confirm_cash_scope(
            discover_cash_scope(entries),
            {"1002": "include", "1012": "include"},
        )

        result = build_cashflow_components(entries, scope)

        self.assertEqual((), result.components)

    def test_full_voucher_without_an_in_scope_cash_leg_does_not_use_one_sided_fallback(self) -> None:
        entries = (
            _component_entry(
                90,
                "V-RESTRICTED",
                "银行存款_保证金户",
                credit_cent=10_000,
                retained_side="cash",
                summary="转出受限账户款项",
            ),
            _component_entry(
                91,
                "V-RESTRICTED",
                "应付账款_供应商",
                debit_cent=10_000,
                item="购买商品、接受劳务支付的现金",
                flow_amount_cent=10_000,
                retained_side="counterpart",
                summary="支付货款",
            ),
        )
        proposal = discover_cash_scope(entries)
        scope = confirm_cash_scope(
            proposal,
            {candidate.account_key: "exclude" for candidate in proposal.candidates},
        )

        result = build_cashflow_components(entries, scope)

        self.assertEqual((), result.components)

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

    def test_one_sided_evidence_without_cash_anchor_requests_cleanup(self) -> None:
        counterpart_entries = component_entries("one_sided_counterpart")
        counterpart_scope = _confirmed_scope("one_sided_counterpart")
        counterpart = build_cashflow_components(
            counterpart_entries, counterpart_scope
        )
        cash = build_cashflow_components(
            component_entries("one_sided_cash"), _confirmed_scope("one_sided_cash")
        ).components[0]
        summary_entries = component_entries("summary_only")
        summary_scope = _confirmed_scope("summary_only")
        summary_only = build_cashflow_components(summary_entries, summary_scope)
        self.assertFalse(counterpart.components)
        self.assertEqual(
            1, len(find_cash_row_cleanup_requests(counterpart_entries, counterpart_scope))
        )
        self.assertEqual((), cash.counterpart_accounts)
        self.assertFalse(summary_only.components)
        self.assertEqual(
            1, len(find_cash_row_cleanup_requests(summary_entries, summary_scope))
        )

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

    def test_standard_flow_item_cannot_replace_a_confirmed_cash_leg(self) -> None:
        entries = component_entries("summary_only_counterpart_direction")
        scope = _confirmed_scope("summary_only_counterpart_direction")
        result = build_cashflow_components(entries, scope)
        self.assertFalse(result.components)
        self.assertEqual(1, len(find_cash_row_cleanup_requests(entries, scope)))

    def test_confirmed_cash_leg_has_priority_and_split_label_is_used_once(self) -> None:
        flow = build_cashflow_components(
            component_entries("flow_amount_differs"), _confirmed_scope("flow_amount_differs")
        )
        split = build_cashflow_components(
            component_entries("split_label_duplication"), _confirmed_scope("split_label_duplication")
        )
        self.assertEqual(20_000, flow.components[0].cash_delta_cent)
        self.assertEqual(1, len(split.components))
        self.assertEqual(25_000, split.components[0].cash_delta_cent)
        self.assertEqual(len(split.components[0].source_keys), len(set(split.components[0].source_keys)))

    def test_mixed_cash_directions_do_not_double_allocate_a_conflicting_flow_label(self) -> None:
        entries = (
            _component_entry(201, "V-MIXED-CASH", "研发支出_差旅费", debit_cent=415_100, flow_amount_cent=20_500, item="收到其他与经营活动有关的现金", retained_side="counterpart", summary="支付报销款"),
            _component_entry(202, "V-MIXED-CASH", "管理费用_差旅费", debit_cent=467_890, flow_amount_cent=2_031_600, item="支付其他与经营活动有关的现金", retained_side="counterpart", summary="支付报销款"),
            _component_entry(203, "V-MIXED-CASH", "1002 银行存款", credit_cent=2_031_600, retained_side="cash", summary="支付报销款"),
            _component_entry(204, "V-MIXED-CASH", "1002 银行存款", debit_cent=20_500, retained_side="cash", summary="收到退款"),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {"1002": "include"})

        result = build_cashflow_components(entries, scope)

        self.assertEqual([-2_031_600, 20_500], sorted(item.cash_delta_cent for item in result.components))
        allocated_by_component: dict[str, int] = defaultdict(int)
        for allocation in result.source_allocations:
            allocated_by_component[allocation.component_id] += allocation.allocated_cent
        self.assertEqual(
            {item.component_id: item.cash_delta_cent for item in result.components},
            dict(allocated_by_component),
        )

    def test_unbalanced_voucher_keeps_determinable_cash_and_marks_anomaly(self) -> None:
        result = build_cashflow_components(
            component_entries("unbalanced_cash_fact"), _confirmed_scope("unbalanced_cash_fact")
        )
        self.assertEqual(30_000, result.components[0].cash_delta_cent)
        self.assertIn("voucher_unbalanced", result.components[0].anomalies)

    def test_counterpart_side_direction_uses_account_side_not_original_item(self) -> None:
        # 原项目故意与借贷事实相反，方向仍必须由实际账户侧确定。
        entries = (
            _component_entry(1, "V1", "销售费用_业务招待费", debit_cent=600_00, item="销售商品、提供劳务收到的现金", flow_amount_cent=600_00, retained_side="counterpart"),
            _component_entry(2, "V2", "合同负债_业务", credit_cent=700000_00, item="购买商品、接受劳务支付的现金", flow_amount_cent=700000_00, retained_side="counterpart"),
        )
        self.assertEqual(-600_00, _signed_flow(entries[0]))
        self.assertEqual(700000_00, _signed_flow(entries[1]))

    def test_red_reversal_sign_follows_account_side_not_original_item(self) -> None:
        refund_in = _component_entry(3, "V3", "其他应收款_其他", debit_cent=-180_00, item="收到其他与经营活动有关的现金", flow_amount_cent=-180_00, retained_side="counterpart")
        refund_out = _component_entry(4, "V4", "应付账款_财务", credit_cent=-29000_00, item="支付其他与经营活动有关的现金", flow_amount_cent=-29000_00, retained_side="counterpart")
        self.assertEqual(180_00, _signed_flow(refund_in))
        self.assertEqual(-29000_00, _signed_flow(refund_out))

    def test_unknown_item_name_cannot_override_account_side(self) -> None:
        entry = _component_entry(5, "V5", "应收款项", credit_cent=12000_00, item="项目甲", flow_amount_cent=12000_00, retained_side="counterpart")
        self.assertEqual(12000_00, _signed_flow(entry))

    def test_flow_direction_source_labels_all_four_cases(self) -> None:
        inflow = _component_entry(10, "V10", "银行存款", debit_cent=100_00, item="销售商品收到的现金", flow_amount_cent=100_00, retained_side="cash")
        outflow = _component_entry(11, "V11", "银行存款", credit_cent=100_00, item="购买商品支付的现金", flow_amount_cent=100_00, retained_side="cash")
        debit_credit = _component_entry(12, "V12", "银行存款", debit_cent=100_00, flow_amount_cent=100_00, retained_side="cash")
        balance = _component_entry(13, "V13", "银行存款", debit_cent=100_00, retained_side="cash")
        self.assertEqual("借贷列+流量金额", flow_direction_source(inflow))
        self.assertEqual("借贷列+流量金额", flow_direction_source(outflow))
        self.assertEqual("借贷列+流量金额", flow_direction_source(debit_credit))
        self.assertEqual("借贷差额", flow_direction_source(balance))

    def test_every_cash_candidate_requires_one_confirmation(self) -> None:
        proposal = discover_cash_scope(component_entries("pure_internal"))
        with self.assertRaisesRegex(ValueError, "等待现金范围确认"):
            confirm_cash_scope(proposal, {})

    def test_restricted_words_on_non_cash_accounts_do_not_create_cash_candidates(self) -> None:
        entries = (
            _component_entry(101, "V-CASH-SCOPE", "其他应收款_保证金及押金"),
            _component_entry(102, "V-CASH-SCOPE", "其他应付款_保证金及押金"),
            _component_entry(103, "V-CASH-SCOPE", "应收票据_质押票据"),
            _component_entry(
                104,
                "V-CASH-SCOPE",
                "银行存款_保证金户",
                debit_cent=10_000,
                retained_side="cash",
            ),
        )

        proposal = discover_cash_scope(entries)

        self.assertEqual(
            ("银行存款_保证金户",),
            tuple(item.account_key for item in proposal.candidates),
        )
        self.assertEqual("confirm", proposal.candidates[0].system_suggestion)

    def test_included_bank_and_excluded_margin_account_create_real_inflow_and_outflow(self) -> None:
        cases = (
            (
                10_000,
                _component_entry(
                    110,
                    "V-MARGIN-IN",
                    "1002 银行存款_基本户",
                    debit_cent=10_000,
                    retained_side="cash",
                    summary="保证金退回基本户",
                ),
                _component_entry(
                    111,
                    "V-MARGIN-IN",
                    "1012 其他货币资金_保证金户",
                    credit_cent=10_000,
                    item="收到其他与经营活动有关的现金",
                    retained_side="counterpart",
                    summary="收回履约保证金",
                ),
            ),
            (
                -10_000,
                _component_entry(
                    120,
                    "V-MARGIN-OUT",
                    "1002 银行存款_基本户",
                    credit_cent=10_000,
                    retained_side="cash",
                    summary="基本户转入保证金户",
                ),
                _component_entry(
                    121,
                    "V-MARGIN-OUT",
                    "1012 其他货币资金_保证金户",
                    debit_cent=10_000,
                    item="支付其他与经营活动有关的现金",
                    retained_side="counterpart",
                    summary="支付履约保证金",
                ),
            ),
        )

        for expected_amount, cash_entry, margin_entry in cases:
            with self.subTest(expected_amount=expected_amount):
                entries = (cash_entry, margin_entry)
                scope = confirm_cash_scope(
                    discover_cash_scope(entries),
                    {"1002": "include", "1012": "exclude"},
                )

                result = build_cashflow_components(entries, scope)

                self.assertEqual(1, len(result.components))
                self.assertEqual(expected_amount, result.components[0].cash_delta_cent)
                self.assertEqual(
                    ("1012 其他货币资金_保证金户",),
                    result.components[0].counterpart_accounts,
                )
                self.assertEqual(
                    expected_amount,
                    sum(item.allocated_cent for item in result.source_allocations),
                )

    def test_interest_of_excluded_margin_account_is_not_included_cashflow(self) -> None:
        entries = (
            _component_entry(
                130,
                "V-MIXED-INTEREST",
                "银行存款_一般户",
                debit_cent=100,
                retained_side="cash",
                summary="收到利息收入",
            ),
            _component_entry(
                131,
                "V-MIXED-INTEREST",
                "财务费用_利息收入",
                debit_cent=-100,
                item="收到其他与经营活动有关的现金",
                flow_amount_cent=100,
                retained_side="counterpart",
                summary="收到利息收入",
            ),
            _component_entry(
                132,
                "V-MIXED-INTEREST",
                "银行存款_保证金户",
                debit_cent=1,
                retained_side="cash",
                summary="收到利息收入",
            ),
            _component_entry(
                133,
                "V-MIXED-INTEREST",
                "财务费用_利息收入",
                debit_cent=-1,
                item="收到其他与经营活动有关的现金",
                flow_amount_cent=1,
                retained_side="counterpart",
                summary="收到利息收入",
            ),
        )
        scope = confirm_cash_scope(
            discover_cash_scope(entries),
            {"银行存款_一般户": "include", "银行存款_保证金户": "exclude"},
        )

        result = build_cashflow_components(entries, scope)

        self.assertEqual(1, len(result.components))
        self.assertEqual(100, result.components[0].cash_delta_cent)
        self.assertEqual(100, sum(item.allocated_cent for item in result.source_allocations))


    def test_accrual_with_real_cash_leg_goes_to_ai_not_excluded(self) -> None:
        # 摘要写"计提"、但凭证有真实现金腿 → 打新标记送 AI，不再 EXCLUDED
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
        self.assertIn("accrual_with_cash_leg", result.components[0].anomalies)
        self.assertNotIn("non_cash", result.components[0].anomalies)
        self.assertFalse(_classify(result.components[0]).excluded)

    def test_non_cash_flow_rows_without_a_confirmed_cash_leg_request_cleanup(self) -> None:
        entries = (
            _component_entry(1, "V2", "1121 应收票据", credit_cent=58_972_968_30, retained_side="counterpart", flow_amount_cent=-58_972_968_30, summary="票据背书抵应付账款"),
            _component_entry(2, "V2", "2202 应付账款", debit_cent=58_972_968_30, retained_side="counterpart", flow_amount_cent=58_972_968_30, summary="票据背书抵应付账款"),
        )
        proposal = discover_cash_scope(entries)
        scope = confirm_cash_scope(proposal, {})
        requests = find_cash_row_cleanup_requests(entries, scope)
        result = build_cashflow_components(entries, scope)
        self.assertEqual(1, len(requests))
        self.assertEqual((entries[0].entry_id, entries[1].entry_id), requests[0].entry_ids)
        self.assertFalse(result.components)

    def test_confirmed_excluded_cash_account_does_not_request_cleanup(self) -> None:
        entries = (
            _component_entry(
                1,
                "V-EXCLUDED-ONLY",
                "1012 其他货币资金_保证金户",
                credit_cent=10_000,
                flow_amount_cent=10_000,
                retained_side="cash",
                item="支付其他与经营活动有关的现金",
                summary="保证金账户对外付款",
            ),
            _component_entry(
                2,
                "V-EXCLUDED-ONLY",
                "其他应付款_供应商",
                debit_cent=10_000,
                flow_amount_cent=10_000,
                retained_side="counterpart",
                item="支付其他与经营活动有关的现金",
                summary="保证金账户对外付款",
            ),
        )
        scope = confirm_cash_scope(
            discover_cash_scope(entries),
            {"1012": "exclude"},
        )

        self.assertFalse(find_cash_row_cleanup_requests(entries, scope))
        self.assertFalse(build_cashflow_components(entries, scope).components)

    def test_named_confirmed_cash_counterpart_is_a_reliable_one_sided_proxy(self) -> None:
        entry = _component_entry(
            1,
            "V-NAMED-CASH",
            "1121 应收票据",
            credit_cent=10_000,
            flow_amount_cent=10_000,
            retained_side="counterpart",
            counterpart_name="1002 银行存款",
            item="销售商品、提供劳务收到的现金",
            summary="票据到期收款",
        )
        scope = confirm_cash_scope(
            discover_cash_scope((entry,)),
            {"1002": "include"},
        )

        self.assertFalse(find_cash_row_cleanup_requests((entry,), scope))
        self.assertEqual(1, len(build_cashflow_components((entry,), scope).components))

    def test_note_receipt_with_cash_counterpart_stays_real_cash(self) -> None:
        # 票据贴现/到期收款：应收票据 + 银行存款对方科目 → 真实现金流，不得误标 non_cash
        from pathlib import Path
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
        self.assertFalse(_classify(result.components[0]).excluded)

    def test_note_only_voucher_with_standard_flow_label_requests_cleanup(self) -> None:
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
        requests = find_cash_row_cleanup_requests(entries, scope)
        result = build_cashflow_components(entries, scope)
        self.assertEqual(1, len(requests))
        self.assertFalse(result.components)

    def test_single_sided_file_profile_cannot_use_standard_flow_rows_as_cash(self) -> None:
        entries = (
            _component_entry(
                1, "V31", "2202 应付账款", debit_cent=58_972_968_30,
                flow_amount_cent=58_972_968_30, retained_side="counterpart",
                item="购建固定资产、无形资产和其他长期资产支付的现金", summary="计提-财务应付",
            ),
            _component_entry(
                2, "V31", "1121 应收票据", credit_cent=48_972_968_30,
                flow_amount_cent=-48_972_968_30, retained_side="counterpart",
                item="购建固定资产、无形资产和其他长期资产支付的现金", summary="计提-票据红冲",
            ),
        )
        scope = confirm_cash_scope(discover_cash_scope(entries), {})
        result = build_cashflow_components(
            entries, scope, single_sided_file_ids=frozenset({"FSYN"})
        )
        self.assertFalse(result.components)
        self.assertEqual(1, len(find_cash_row_cleanup_requests(entries, scope)))

    def test_red_flow_amount_with_standard_item_still_requires_a_cash_leg(self) -> None:
        entries = (
            _component_entry(
                1, "V41", "2202 应付账款", debit_cent=58_972_968_30,
                flow_amount_cent=58_972_968_30, retained_side="counterpart",
                item="购建固定资产、无形资产和其他长期资产支付的现金", summary="财务应付",
            ),
            _component_entry(
                2, "V41", "1121 应收票据", credit_cent=48_972_968_30,
                flow_amount_cent=-48_972_968_30, retained_side="counterpart",
                item="购建固定资产、无形资产和其他长期资产支付的现金", summary="票据红冲",
            ),
        )
        result = build_cashflow_components(
            entries, confirm_cash_scope(discover_cash_scope(entries), {}),
            single_sided_file_ids=frozenset({"FSYN"}),
        )
        self.assertFalse(result.components)
        self.assertEqual(
            1,
            len(
                find_cash_row_cleanup_requests(
                    entries, confirm_cash_scope(discover_cash_scope(entries), {})
                )
            ),
        )

    def test_rough_reconciliation_applies_only_to_counterpart_flow_detail(self) -> None:
        from cashflow_direct.components import compute_rough_reconciliation
        from cashflow_direct.models import EvidenceProfile

        entries = (
            _component_entry(
                1, "V51", "2202 应付账款", debit_cent=10_000_00,
                flow_amount_cent=10_000_00, retained_side="counterpart",
                item="购买商品、接受劳务支付的现金", summary="付款",
            ),
        )
        detail_profile = EvidenceProfile(
            False, False, True, frozenset(), frozenset({"counterpart"}), True, False, False
        )
        rough = compute_rough_reconciliation(
            entries, {"FSYN": detail_profile}, opening_cent=20_000_00, closing_cent=10_000_00, fx_cent=0
        )
        self.assertTrue(rough.applicable)
        self.assertEqual("相符", rough.status)
        self.assertEqual(-10_000_00, rough.detail_sum_cent)
        journal_profile = EvidenceProfile(
            True, False, False, frozenset(), frozenset({"cash", "counterpart"}), False, False, False
        )
        journal_rough = compute_rough_reconciliation(
            entries, {"f1": journal_profile}, opening_cent=20_000_00, closing_cent=10_000_00, fx_cent=0
        )
        self.assertFalse(journal_rough.applicable)
        self.assertEqual("不适用", journal_rough.status)

    def test_journal_note_endorsement_without_cash_produces_no_component(self) -> None:
        # 序时账纯票据背书（票据腿+往来腿、无现金科目、无流量金额）不产生组件、不进正表
        entries = (
            _component_entry(
                1, "V61", "1121 应收票据", credit_cent=10_000_00,
                retained_side="counterpart", summary="票据背书",
            ),
            _component_entry(
                2, "V61", "2202 应付账款", debit_cent=10_000_00,
                retained_side="counterpart", summary="票据背书",
            ),
        )
        result = build_cashflow_components(
            entries, confirm_cash_scope(discover_cash_scope(entries), {})
        )
        self.assertEqual((), result.components)


def test_cash_equivalent_investment_is_discovered_and_needs_four_criteria() -> None:
    entries = (
        _component_entry(
            1,
            "V-CASH-EQUIVALENT",
            "1101 交易性金融资产_三个月内到期债券",
            debit_cent=10_000,
            summary="购买三个月内到期债券",
        ),
    )

    proposal = discover_cash_scope(entries)

    assert len(proposal.candidates) == 1
    candidate = proposal.candidates[0]
    assert candidate.system_suggestion == "confirm_cash_equivalent"
    with pytest.raises(ValueError, match="四项条件"):
        confirm_cash_scope(proposal, {candidate.account_key: "include"})

    scope = confirm_cash_scope(
        proposal,
        {
            candidate.account_key: {
                "status": "include",
                "short_term": True,
                "high_liquidity": True,
                "known_cash_amount": True,
                "low_value_change_risk": True,
                "available_for_payment": True,
            }
        },
    )
    assert candidate.account_key in scope.included_keys


def test_pledge_status_change_requires_period_specific_scope_cleanup() -> None:
    entries = (
        _component_entry(
            1,
            "V-PLEDGE",
            "1101 定期存款",
            credit_cent=10_000,
            summary="定期存单质押",
        ),
        _component_entry(
            2,
            "V-UNPLEDGE",
            "1101 定期存款",
            debit_cent=10_000,
            summary="定期存单解除质押",
        ),
    )

    proposal = discover_cash_scope(entries)
    candidate = proposal.candidates[0]

    assert candidate.system_suggestion == "clarify_period_change"
    with pytest.raises(ValueError, match="按期间"):
        confirm_cash_scope(
            proposal,
            {
                candidate.account_key: {
                    "status": "include",
                    "short_term": True,
                    "high_liquidity": True,
                    "known_cash_amount": True,
                    "low_value_change_risk": True,
                    "available_for_payment": True,
                }
            },
        )
    scope = confirm_cash_scope(proposal, {candidate.account_key: "exclude"})
    assert candidate.account_key in scope.excluded_keys


if __name__ == "__main__":
    unittest.main()
