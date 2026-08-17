from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from cashflow_direct.consistency import (
    ConsistencyResult,
    build_consistency_adjudication_tasks,
    build_consistency_tasks,
    find_consistency_groups,
    merge_consistency_results,
    resolve_consistency_groups,
    validate_consistency_results,
)
from cashflow_direct.classification import load_rule_pack
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    MaterialityAmounts,
)


MATERIALITY = MaterialityAmounts(
    overall_cent=10_000_000,
    performance_cent=5_000_000,
    trivial_cent=500_000,
)


def component(
    component_id: str,
    amount_cent: int,
    *,
    summary: str = "同一项业务退款",
    voucher_key: str = "VCH-1",
    account: str = "预付账款",
    source_file: str = "FILE-1",
) -> CashflowComponent:
    return CashflowComponent(
        component_id=component_id,
        voucher_key=voucher_key,
        summary=summary,
        cash_delta_cent=amount_cent,
        counterpart_accounts=(account,),
        original_item_text="收到其他与经营活动有关的现金",
        source_keys=(f"ENT-{component_id}",),
        evidence_strength="medium",
        voucher_date="2026/6/15",
        voucher_no="70",
        source_file_ids=(source_file,),
    )


def decision(
    component_id: str,
    item_id: str,
    *,
    excluded: bool = False,
) -> ClassificationDecision:
    names = {
        "CFO-03": "收到其他与经营活动有关的现金",
        "CFO-04": "购买商品、接受劳务支付的现金",
        "CFI-06": "购建固定资产、无形资产和其他长期资产支付的现金",
    }
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=item_id,
        system_item_name=names[item_id],
        normal_direction="outflow" if item_id == "CFI-06" else "inflow",
        matched_rule_id="TEST",
        reason="测试判断",
        evidence_level="medium",
        excluded=excluded,
    )


class ConsistencyGroupingTests(unittest.TestCase):
    def test_same_source_voucher_and_summary_with_different_items_forms_one_group(self) -> None:
        components = (
            component("C1", 71_760_000, account="预付账款"),
            component("C2", -21_760_000, account="应付账款"),
        )
        decisions = (decision("C1", "CFO-03"), decision("C2", "CFI-06"))

        groups = find_consistency_groups(components, decisions, MATERIALITY)

        self.assertEqual(1, len(groups))
        self.assertEqual(("C1", "C2"), groups[0].component_ids)
        self.assertEqual(93_520_000, groups[0].gross_cent)
        self.assertEqual(50_000_000, groups[0].net_cent)
        self.assertEqual("double_high_required", groups[0].tier)

    def test_different_summary_or_source_never_forces_a_group(self) -> None:
        components = (
            component("C1", 6_000_000),
            component("C2", -2_000_000, summary="另一项业务退款"),
            component("C3", -2_000_000, source_file="FILE-2"),
        )
        decisions = (
            decision("C1", "CFO-03"),
            decision("C2", "CFI-06"),
            decision("C3", "CFI-06"),
        )

        self.assertEqual((), find_consistency_groups(components, decisions, MATERIALITY))

    def test_blank_summary_equal_items_and_excluded_items_do_not_create_groups(self) -> None:
        cases = (
            (
                (component("C1", 6_000_000, summary=""), component("C2", -1_000_000, summary="")),
                (decision("C1", "CFO-03"), decision("C2", "CFI-06")),
            ),
            (
                (component("C1", 6_000_000), component("C2", -1_000_000)),
                (decision("C1", "CFO-03"), decision("C2", "CFO-03")),
            ),
            (
                (component("C1", 6_000_000), component("C2", -1_000_000)),
                (decision("C1", "CFO-03"), decision("C2", "CFI-06", excluded=True)),
            ),
        )
        for components, decisions in cases:
            with self.subTest(components=components, decisions=decisions):
                self.assertEqual(
                    (), find_consistency_groups(components, decisions, MATERIALITY)
                )

    def test_gross_amount_uses_all_three_materiality_levels(self) -> None:
        cases = (
            (499_999, "trace_only"),
            (500_000, "first_review"),
            (4_999_999, "first_review"),
            (5_000_000, "adjudication_required"),
            (9_999_999, "adjudication_required"),
            (10_000_000, "double_high_required"),
        )
        for gross_cent, expected_tier in cases:
            first = gross_cent // 2
            second = gross_cent - first
            components = (component("C1", first), component("C2", -second))
            decisions = (decision("C1", "CFO-03"), decision("C2", "CFI-06"))
            with self.subTest(gross_cent=gross_cent):
                group = find_consistency_groups(
                    components, decisions, MATERIALITY
                )[0]
                self.assertEqual(expected_tier, group.tier)

    def test_ai_task_contains_the_whole_group_context(self) -> None:
        components = (
            component("C1", 71_760_000, account="预付账款"),
            component("C2", -21_760_000, account="应付账款"),
        )
        decisions = (decision("C1", "CFO-03"), decision("C2", "CFI-06"))
        groups = find_consistency_groups(components, decisions, MATERIALITY)

        tasks = build_consistency_tasks(groups)

        self.assertEqual(1, len(tasks))
        self.assertIn("C1", tasks[0].context)
        self.assertIn("C2", tasks[0].context)
        self.assertIn("预付账款", tasks[0].context)
        self.assertIn("应付账款", tasks[0].context)
        self.assertIn("2026/6/15", tasks[0].context)
        self.assertIn("凭证号：70", tasks[0].context)
        self.assertIn("收到其他与经营活动有关的现金", tasks[0].context)
        self.assertEqual(
            (), build_consistency_tasks((replace(groups[0], tier="trace_only"),))
        )


class ConsistencyValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        components = (
            component("C1", 71_760_000),
            component("C2", -21_760_000, account="应付账款"),
        )
        decisions = (decision("C1", "CFO-03"), decision("C2", "CFI-06"))
        self.task = build_consistency_tasks(
            find_consistency_groups(components, decisions, MATERIALITY)
        )[0]
        self.valid_items = {"CFO-03", "CFO-04", "CFI-06"}

    def payload(self, **changes: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "task_id": self.task.task_id,
            "group_id": self.task.group_id,
            "assignments": {"C1": "CFI-06", "C2": "CFI-06"},
            "reason": "整组业务实质一致",
            "confidence": "high",
        }
        payload.update(changes)
        return payload

    def test_complete_group_result_is_valid(self) -> None:
        result = validate_consistency_results(
            (self.task,), (self.payload(),), self.valid_items
        )

        self.assertEqual("AI 已完成", result.status)
        self.assertEqual((("C1", "CFI-06"), ("C2", "CFI-06")), result.valid_results[0].assignments)

    def test_missing_component_illegal_item_and_blank_reason_are_invalid(self) -> None:
        bad_payloads = (
            self.payload(assignments={"C1": "CFI-06"}),
            self.payload(assignments={"C1": "CFI-06", "C2": "NOT-A-LEAF"}),
            self.payload(reason=" "),
            self.payload(confidence="certain"),
        )
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                result = validate_consistency_results(
                    (self.task,), (payload,), self.valid_items
                )
                self.assertEqual("AI 未完成", result.status)
                self.assertEqual((self.task.task_id,), result.invalid_ids)

    def test_duplicate_and_missing_results_never_complete(self) -> None:
        duplicate = validate_consistency_results(
            (self.task,), (self.payload(), self.payload()), self.valid_items
        )
        missing = validate_consistency_results((self.task,), (), self.valid_items)

        self.assertEqual((self.task.task_id,), duplicate.duplicate_ids)
        self.assertEqual((self.task.task_id,), missing.missing_ids)
        self.assertEqual("AI 未完成", duplicate.status)
        self.assertEqual("AI 未完成", missing.status)

    def test_partial_batches_merge_without_resubmitting_completed_groups(self) -> None:
        components = (
            component("C3", 8_000_000, voucher_key="VCH-2"),
            component("C4", -2_000_000, voucher_key="VCH-2"),
        )
        decisions = (decision("C3", "CFO-03"), decision("C4", "CFI-06"))
        second_task = build_consistency_tasks(
            find_consistency_groups(components, decisions, MATERIALITY)
        )[0]
        second_payload = {
            "task_id": second_task.task_id,
            "group_id": second_task.group_id,
            "assignments": {"C3": "CFI-06", "C4": "CFI-06"},
            "reason": "第二组完成",
            "confidence": "high",
        }
        first_validation = merge_consistency_results(
            (self.task, second_task), (), (self.payload(),), self.valid_items
        )
        completed = merge_consistency_results(
            (self.task, second_task),
            first_validation.valid_results,
            (second_payload,),
            self.valid_items,
        )

        self.assertEqual(1, len(first_validation.missing_ids))
        self.assertEqual("AI 已完成", completed.status)
        self.assertEqual(2, len(completed.valid_results))


class ConsistencyResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rules = load_rule_pack(Path(__file__).parents[1])

    def case(self, gross_cent: int) -> tuple[object, tuple[ClassificationDecision, ...]]:
        first = gross_cent * 3 // 4
        second = gross_cent - first
        components = (component("C1", first), component("C2", -second))
        decisions = (decision("C1", "CFO-03"), decision("C2", "CFO-04"))
        group = find_consistency_groups(components, decisions, MATERIALITY)[0]
        return group, decisions

    @staticmethod
    def result(
        task_id: str,
        group_id: str,
        item_id: str,
        confidence: str,
    ) -> ConsistencyResult:
        return ConsistencyResult(
            task_id=task_id,
            group_id=group_id,
            assignments=(("C1", item_id), ("C2", item_id)),
            reason="整组属于同一业务",
            confidence=confidence,
        )

    def test_first_review_applies_medium_result_but_low_result_only_leaves_trace(self) -> None:
        group, decisions = self.case(1_000_000)
        task = build_consistency_tasks((group,))[0]
        medium = self.result(task.task_id, group.group_id, "CFI-06", "medium")
        low = self.result(task.task_id, group.group_id, "CFI-06", "low")

        applied = resolve_consistency_groups(
            (group,), decisions, (medium,), (), self.rules
        )
        retained = resolve_consistency_groups(
            (group,), decisions, (low,), (), self.rules
        )

        self.assertEqual({"CFI-06"}, {item.system_item_id for item in applied.decisions})
        self.assertEqual("consistency_review", applied.decisions[0].decision_source)
        self.assertEqual(
            {"CFO-03", "CFO-04"},
            {item.system_item_id for item in retained.decisions},
        )
        self.assertEqual((), retained.unresolved)

    def test_performance_level_requires_high_adjudication_or_goes_to_human(self) -> None:
        group, decisions = self.case(6_000_000)
        first_task = build_consistency_tasks((group,))[0]
        first = self.result(first_task.task_id, group.group_id, "CFI-06", "high")
        second_task = build_consistency_adjudication_tasks((group,), (first,))[0]
        medium_second = self.result(
            second_task.task_id, group.group_id, "CFI-06", "medium"
        )
        high_second = self.result(
            second_task.task_id, group.group_id, "CFI-06", "high"
        )

        unresolved = resolve_consistency_groups(
            (group,), decisions, (first,), (medium_second,), self.rules
        )
        resolved = resolve_consistency_groups(
            (group,), decisions, (first,), (high_second,), self.rules
        )

        self.assertEqual((group.group_id,), tuple(item.group_id for item in unresolved.unresolved))
        self.assertEqual({"CFI-06"}, {item.system_item_id for item in resolved.decisions})
        self.assertEqual("consistency_adjudication", resolved.decisions[0].decision_source)

    def test_overall_level_requires_two_high_results_with_identical_assignments(self) -> None:
        group, decisions = self.case(12_000_000)
        first_task = build_consistency_tasks((group,))[0]
        first = self.result(first_task.task_id, group.group_id, "CFI-06", "high")
        second_task = build_consistency_adjudication_tasks((group,), (first,))[0]
        agreeing = self.result(
            second_task.task_id, group.group_id, "CFI-06", "high"
        )
        disagreeing = ConsistencyResult(
            task_id=second_task.task_id,
            group_id=group.group_id,
            assignments=(("C1", "CFI-06"), ("C2", "CFO-04")),
            reason="两条业务实质不同",
            confidence="high",
        )

        resolved = resolve_consistency_groups(
            (group,), decisions, (first,), (agreeing,), self.rules
        )
        unresolved = resolve_consistency_groups(
            (group,), decisions, (first,), (disagreeing,), self.rules
        )

        self.assertEqual((), resolved.unresolved)
        self.assertEqual(1, len(unresolved.unresolved))
        candidates = dict(unresolved.unresolved[0].candidate_item_ids)
        self.assertIn("CFI-06", candidates["C2"])
        self.assertIn("CFO-04", candidates["C2"])

    def test_different_high_evidence_items_are_never_propagated_without_group_results(self) -> None:
        group, decisions = self.case(12_000_000)
        decisions = tuple(replace(item, evidence_level="high") for item in decisions)

        outcome = resolve_consistency_groups(
            (group,), decisions, (), (), self.rules
        )

        self.assertEqual(
            {"CFO-03", "CFO-04"},
            {item.system_item_id for item in outcome.decisions},
        )
        self.assertEqual(1, len(outcome.unresolved))


if __name__ == "__main__":
    unittest.main()
