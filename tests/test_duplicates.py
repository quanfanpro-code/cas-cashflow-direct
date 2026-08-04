from __future__ import annotations

import unittest

from cashflow_direct.duplicates import (
    apply_duplicate_decisions,
    assign_duplicate_items,
    find_suspected_duplicates,
)
from cashflow_direct.models import ClassificationDecision
from tests.fixture_factory import duplicate_components


class DuplicateTests(unittest.TestCase):
    def test_suspected_cross_file_duplicate_defaults_to_keep(self) -> None:
        groups = find_suspected_duplicates(
            duplicate_components(amount_cent=80_000_000), performance_cent=75_000_000
        )
        self.assertEqual(1, len(groups))
        self.assertEqual("keep", groups[0].default_decision)
        self.assertTrue(groups[0].blocks_manual_completion)
        self.assertEqual((), apply_duplicate_decisions(groups, {}))

    def test_user_exclusion_creates_one_adjustment_not_source_deletion(self) -> None:
        groups = find_suspected_duplicates(
            duplicate_components(amount_cent=80_000_000), performance_cent=75_000_000
        )
        adjustments = apply_duplicate_decisions(groups, {groups[0].group_id: "exclude"})
        self.assertEqual(1, len(adjustments))
        self.assertEqual(-80_000_000, adjustments[0].cash_delta_cent)

    def test_signature_protects_date_voucher_direction_and_amount(self) -> None:
        variants = (
            duplicate_components(100, second_date="2026-04-02"),
            duplicate_components(100, second_voucher="记-21"),
            duplicate_components(100, second_amount_cent=-100),
            duplicate_components(100, second_amount_cent=101),
        )
        for components in variants:
            with self.subTest(components=components):
                self.assertEqual((), find_suspected_duplicates(components, 1))

    def test_common_spacing_and_punctuation_normalize_but_same_file_split_does_not_group(self) -> None:
        self.assertEqual(1, len(find_suspected_duplicates(duplicate_components(100), 1)))
        self.assertEqual((), find_suspected_duplicates(duplicate_components(100, same_file=True), 1))

    def test_below_performance_is_listed_without_blocking(self) -> None:
        groups = find_suspected_duplicates(duplicate_components(74_999_999), 75_000_000)
        self.assertEqual(1, len(groups))
        self.assertFalse(groups[0].blocks_manual_completion)

    def test_duplicate_group_uses_final_standard_item_not_raw_label(self) -> None:
        components = duplicate_components(100)
        groups = find_suspected_duplicates(components, 1)
        decisions = tuple(
            ClassificationDecision(
                component_id=component.component_id,
                system_item_id="CFO-03",
                system_item_name="收到其他与经营活动有关的现金",
                normal_direction="inflow",
                matched_rule_id="CFO-03-CURRENT",
                reason="匿名证据",
                evidence_level="high",
            )
            for component in components
        )
        assigned = assign_duplicate_items(groups, decisions)
        self.assertEqual("CFO-03", assigned[0].item_id)


if __name__ == "__main__":
    unittest.main()
