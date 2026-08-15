from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cashflow_direct.ai_review import (
    AIResult,
    build_adjudication_tasks,
    chunk_ai_tasks,
    redact_text,
    resolve_automatic_decisions,
    select_ai_tasks,
    validate_ai_results,
    write_ai_tasks_jsonl,
)
from cashflow_direct.materiality import build_review_batches
from cashflow_direct.models import MaterialityAmounts
from cashflow_direct.pipeline import _review_text_pattern
from tests.fixture_factory import ai_case


class AIAndMaterialityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.materiality = MaterialityAmounts(
            overall_cent=100_000_000,
            performance_cent=75_000_000,
            trivial_cent=5_000_000,
        )

    def test_weak_labeled_selection_uses_three_bands(self) -> None:
        cases = [
            ai_case("below", 4_999_999, weak=True, anomaly=True),
            ai_case("middle_normal", 5_000_000, weak=True, anomaly=False),
            ai_case("middle_anomaly", 5_000_000, weak=True, anomaly=True),
            ai_case("performance", 75_000_000, weak=True, anomaly=False),
        ]
        tasks = select_ai_tasks(
            [case.component for case in cases],
            [case.decision for case in cases],
            self.materiality,
        )
        self.assertEqual({"middle_anomaly", "performance"}, {task.component_id for task in tasks})

    def test_unlabeled_ambiguous_at_trivial_is_selected_but_strong_is_not(self) -> None:
        ambiguous = ai_case("type_a", 5_000_000, weak=True, anomaly=False, labeled=False)
        strong = ai_case("strong", 80_000_000, weak=False, anomaly=False, labeled=False)
        tasks = select_ai_tasks(
            [ambiguous.component, strong.component],
            [ambiguous.decision, strong.decision],
            self.materiality,
        )
        self.assertEqual(["type_a"], [task.component_id for task in tasks])

    def test_batches_never_exceed_25(self) -> None:
        tasks = tuple(ai_case(str(index), 80_000_000, weak=True, anomaly=False).task for index in range(61))
        self.assertEqual([25, 25, 11], [len(batch) for batch in chunk_ai_tasks(tasks)])

    def test_validation_closes_every_id_and_jsonl_is_bom_encoded(self) -> None:
        tasks = tuple(ai_case(str(index), 80_000_000, weak=True, anomaly=False).task for index in range(3))
        payloads = [
            {"task_id": tasks[0].task_id, "component_id": "0", "item_id": "CFO-03", "reason": "摘要支持", "confidence": "high"},
            {"task_id": tasks[1].task_id, "component_id": "1", "item_id": ["CFO-03", "CFI-05"], "reason": "多选", "confidence": "low"},
        ]
        validation = validate_ai_results(tasks, payloads, {"CFO-03", "CFI-05"})
        self.assertEqual(1, len(validation.valid_results))
        self.assertEqual({tasks[1].task_id, tasks[2].task_id}, set(validation.missing_ids))
        self.assertEqual("AI 未完成", validation.status)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AI任务.jsonl"
            write_ai_tasks_jsonl(path, tasks)
            self.assertTrue(path.read_bytes().startswith(bytes.fromhex("EFBBBF")))
            self.assertEqual(3, len(path.read_text(encoding="utf-8-sig").splitlines()))

    def test_sensitive_numbers_are_redacted_before_ai_request(self) -> None:
        text = "电话13800138000，身份证510101199001011234，账号6222021234567890123"
        masked = redact_text(text)
        self.assertNotIn("13800138000", masked)
        self.assertNotIn("510101199001011234", masked)
        self.assertNotIn("6222021234567890123", masked)

    def test_clear_adjudication_resolves_conflict_without_human(self) -> None:
        case = ai_case("conflict", 80_000_000, weak=True, anomaly=True)
        ai_result = AIResult(case.task.task_id, "conflict", "CFI-05", "摘要更支持投资", "medium")
        adjudications = build_adjudication_tasks([case.decision], [ai_result])
        self.assertEqual(1, len(adjudications))
        adjudicated = AIResult(adjudications[0].task_id, "conflict", "CFI-05", "投资证据明确", "high")
        resolved = resolve_automatic_decisions([case.decision], [ai_result], [adjudicated])
        self.assertTrue(resolved[0].resolved)
        self.assertEqual("CFI-05", resolved[0].system_item_id)

    def test_adjudication_cannot_choose_an_unrelated_third_item(self) -> None:
        case = ai_case("guard-third", 80_000_000, weak=True, anomaly=True)
        ai_result = AIResult(case.task.task_id, "guard-third", "CFI-05", "首次判断为投资", "high")
        adjudication = AIResult("ADJ-THIRD", "guard-third", "CFF-01", "改判筹资", "high")
        resolved = resolve_automatic_decisions([case.decision], [ai_result], [adjudication])
        self.assertFalse(resolved[0].resolved)
        self.assertEqual("CFO-03", resolved[0].system_item_id)

    def test_adjudication_task_keeps_original_transaction_context(self) -> None:
        case = ai_case("context", 80_000_000, weak=True, anomaly=True)
        ai_result = AIResult(case.task.task_id, "context", "CFI-05", "首次判断为投资", "high")
        task = build_adjudication_tasks([case.decision], [ai_result], [case.task])[0]
        self.assertIn("摘要：匿名往来事项", task.context)
        self.assertIn("系统证据", task.context)
        self.assertIn("AI 证据", task.context)

    def test_low_confidence_adjudication_does_not_override_high_evidence_rule(self) -> None:
        case = ai_case("guarded", 80_000_000, weak=False, anomaly=True)
        ai_result = AIResult(case.task.task_id, "guarded", "CFI-05", "可能属于投资", "low")
        adjudication = AIResult("ADJ-1", "guarded", "CFI-05", "仍然不确定", "low")
        resolved = resolve_automatic_decisions([case.decision], [ai_result], [adjudication])
        self.assertFalse(resolved[0].resolved)
        self.assertEqual("CFO-03", resolved[0].system_item_id)

    def test_unresolved_below_performance_never_goes_to_human(self) -> None:
        unresolved = [ai_case("small", 74_999_999, weak=True, anomaly=True).unresolved]
        self.assertEqual((), build_review_batches(unresolved, performance_cent=75_000_000))

    def test_only_strictly_homogeneous_major_residuals_are_grouped(self) -> None:
        same_a = ai_case("a", 40_000_000, True, True).unresolved
        same_b = ai_case("b", 40_000_000, True, True).unresolved
        different = ai_case(
            "c", 80_000_000, True, True, summary_pattern="投资词", alternatives=("CFO-03", "CFI-01")
        ).unresolved
        batches = build_review_batches([same_a, same_b, different], 75_000_000)
        self.assertEqual(2, len(batches))
        grouped = next(batch for batch in batches if set(batch.component_ids) == {"a", "b"})
        self.assertEqual(80_000_000, grouped.worst_case_impact_cent)

    def test_review_pattern_keeps_full_business_text_except_dates_and_numbers(self) -> None:
        first = _review_text_pattern("支付甲公司服务费 2026-01-01 1,000元")
        second = _review_text_pattern("支付乙公司服务费 2026-02-02 2,000元")
        self.assertNotEqual(first, second)
        self.assertNotIn("2026", first)
        self.assertNotIn("1000", first)


    def test_label_rule_conflict_joins_ai_regardless_of_performance(self) -> None:
        # 标签与高证据规则冲突（≥明显微小临界值）→ 入选 AI，不受 performance 门槛限制
        from cashflow_direct.models import ClassificationDecision
        from tests.fixture_factory import cashflow_component

        component = cashflow_component("税收滞纳金", -6_000_000, ("营业外支出",),
                                       original_item_text="支付的各项税费", component_id="CFLX")
        decision = ClassificationDecision(
            component_id="CFLX", system_item_id="CFO-06", system_item_name="支付的各项税费",
            normal_direction="outflow", matched_rule_id="LABEL-RULE-CONFLICT",
            reason="冲突", evidence_level="medium", resolved=False,
        )
        tasks = select_ai_tasks([component], [decision], self.materiality)
        self.assertEqual(["CFLX"], [task.component_id for task in tasks])

    def test_label_rule_conflict_below_trivial_is_not_sent(self) -> None:
        # 冲突金额低于明显微小临界值 → 不送 AI
        from cashflow_direct.models import ClassificationDecision
        from tests.fixture_factory import cashflow_component

        component = cashflow_component("税收滞纳金", -4_999_999, ("营业外支出",),
                                       original_item_text="支付的各项税费", component_id="CFLY")
        decision = ClassificationDecision(
            component_id="CFLY", system_item_id="CFO-06", system_item_name="支付的各项税费",
            normal_direction="outflow", matched_rule_id="LABEL-RULE-CONFLICT",
            reason="冲突", evidence_level="medium", resolved=False,
        )
        tasks = select_ai_tasks([component], [decision], self.materiality)
        self.assertEqual((), tasks)

if __name__ == "__main__":
    unittest.main()
