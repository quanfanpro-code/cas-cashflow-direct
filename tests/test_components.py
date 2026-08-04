from __future__ import annotations

import unittest

from cashflow_direct.components import build_cashflow_components, confirm_cash_scope, discover_cash_scope
from tests.fixture_factory import component_entries


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

    def test_every_cash_candidate_requires_one_confirmation(self) -> None:
        proposal = discover_cash_scope(component_entries("pure_internal"))
        with self.assertRaisesRegex(ValueError, "等待现金范围确认"):
            confirm_cash_scope(proposal, {})


if __name__ == "__main__":
    unittest.main()
