from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from cashflow_direct.ai_review import (
    AIResult,
    build_adjudication_tasks,
    chunk_ai_tasks,
    redact_text,
    resolve_automatic_decisions,
    select_ai_tasks,
    validate_ai_results,
    validate_basis_text,
    write_ai_tasks_jsonl,
)
from cashflow_direct.classification import load_rule_pack
from cashflow_direct.materiality import build_review_batches
from cashflow_direct.models import (
    ClassificationDecision,
    MaterialityAmounts,
    UnresolvedDecision,
)
from cashflow_direct.pipeline import _review_text_pattern
from tests.fixture_factory import ai_case, cashflow_component


# 裁决改判后名称修复用的项目名映射（与正表项目一致的中文名）
ITEM_NAMES = {
    "CFO-03": "收到其他与经营活动有关的现金",
    "CFI-05": "支付其他与投资活动有关的现金",
    "CFF-01": "吸收投资收到的现金",
}

ROOT = Path(__file__).resolve().parents[1]


def _routing_case(component_id, amount, *, score, label_kept=False,
                  rule_id="LABEL-KEPT-INSUFFICIENT-EVIDENCE", anomaly=False, weak=False,
                  summary="同类业务", original="购买商品、接受劳务支付的现金"):
    """复核路由用例夹具：默认"保留原标签的冲突"场景，流出方向。"""
    component = cashflow_component(
        summary, amount, ("应付账款_甲",), original_item_text=original,
        anomalies=("direction_anomaly",) if anomaly else (),
        evidence_strength="weak" if weak else "strong",
        component_id=component_id,
    )
    decision = ClassificationDecision(
        component_id=component_id, system_item_id="CFO-04",
        system_item_name="购买商品、接受劳务支付的现金", normal_direction="outflow",
        matched_rule_id=rule_id, reason="测试", evidence_level="medium",
        evidence_score=score, label_kept=label_kept, resolved=False,
    )
    return component, decision


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

    def test_unlabeled_direction_fallback_uses_performance_threshold(self) -> None:
        below = ai_case("below", 5_000_000, weak=True, anomaly=False, labeled=False)
        ambiguous = ai_case("type_a", 75_000_000, weak=True, anomaly=False, labeled=False)
        strong = ai_case("strong", 80_000_000, weak=False, anomaly=False, labeled=False)
        tasks = select_ai_tasks(
            [below.component, ambiguous.component, strong.component],
            [below.decision, ambiguous.decision, strong.decision],
            self.materiality,
        )
        self.assertEqual(["type_a"], [task.component_id for task in tasks])

    def test_high_business_conflicts_use_trivial_materiality_threshold(self) -> None:
        materiality = MaterialityAmounts(10_000_000, 5_000_000, 500_000)
        below = cashflow_component("冲突", 499_999, component_id="HIGH-BELOW")
        at = cashflow_component("冲突", 500_000, component_id="HIGH-AT")
        decisions = [
            ClassificationDecision(
                component.component_id,
                "CFO-03",
                "收到其他与经营活动有关的现金",
                "inflow",
                matched_rule_id,
                "业务证据冲突",
                "medium",
                # 复核路由新口径：冲突 40–69 分档才适用明显微小临界值门槛
                evidence_score=55,
            )
            for component, matched_rule_id in (
                (below, "BUSINESS-RULE-CONFLICT"),
                (at, "BUSINESS-RULE-CONFLICT"),
            )
        ]

        tasks = select_ai_tasks([below, at], decisions, materiality)

        self.assertEqual(["HIGH-AT"], [task.component_id for task in tasks])

    def test_medium_and_low_evidence_use_performance_materiality_threshold(self) -> None:
        materiality = MaterialityAmounts(10_000_000, 5_000_000, 500_000)
        components = [
            cashflow_component("往来款", 4_999_999, component_id="MEDIUM-BELOW"),
            cashflow_component("往来款", 5_000_000, component_id="MEDIUM-AT"),
            cashflow_component("普通业务", 4_999_999, component_id="LOW-BELOW"),
            cashflow_component("普通业务", 5_000_000, component_id="LOW-AT"),
        ]
        decisions = [
            ClassificationDecision(
                component.component_id,
                "CFO-03",
                "收到其他与经营活动有关的现金",
                "inflow",
                "CFO-03-CURRENT" if "MEDIUM" in component.component_id else "ORIGINAL-LABEL-FALLBACK",
                "暂定分类",
                "medium" if "MEDIUM" in component.component_id else "low",
            )
            for component in components
        ]

        tasks = select_ai_tasks(components, decisions, materiality)

        self.assertEqual(
            {"MEDIUM-AT", "LOW-AT"},
            {task.component_id for task in tasks},
        )

    def test_original_label_agreement_does_not_raise_medium_business_evidence(self) -> None:
        materiality = MaterialityAmounts(10_000_000, 5_000_000, 500_000)
        component = cashflow_component(
            "收到保证金",
            5_000_000,
            original_item_text="收到其他与经营活动有关的现金",
            component_id="MEDIUM-AGREES",
        )
        decision = ClassificationDecision(
            component.component_id,
            "CFO-03",
            "收到其他与经营活动有关的现金",
            "inflow",
            "CFO-03-CURRENT",
            "业务证据为中等；原标签一致，仅作补充",
            "medium",
        )

        tasks = select_ai_tasks([component], [decision], materiality)

        self.assertEqual(["MEDIUM-AGREES"], [task.component_id for task in tasks])

    def test_batches_never_exceed_25(self) -> None:
        tasks = tuple(ai_case(str(index), 80_000_000, weak=True, anomaly=False).task for index in range(61))
        self.assertEqual([25, 25, 11], [len(batch) for batch in chunk_ai_tasks(tasks)])

    def test_validation_closes_every_id_and_jsonl_is_bom_encoded(self) -> None:
        tasks = tuple(ai_case(str(index), 80_000_000, weak=True, anomaly=False).task for index in range(3))
        payloads = [
            {"task_id": tasks[0].task_id, "component_id": "0", "item_id": "CFO-03", "reason": "知识库第3行：往来款性质", "confidence": "high"},
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
        resolved = resolve_automatic_decisions([case.decision], [ai_result], [adjudicated], ITEM_NAMES)
        self.assertTrue(resolved[0].resolved)
        self.assertEqual("CFI-05", resolved[0].system_item_id)

    def test_ai_agreement_closes_a_previously_unresolved_decision(self) -> None:
        case = ai_case("agreement", 80_000_000, weak=True, anomaly=True)
        pending = replace(case.decision, resolved=False, decision_source="ai_pending")
        ai_result = AIResult(
            case.task.task_id,
            "agreement",
            "CFO-03",
            "AI 与自动判断一致",
            "high",
        )

        resolved = resolve_automatic_decisions([pending], [ai_result], [], ITEM_NAMES)

        self.assertTrue(resolved[0].resolved)
        self.assertEqual("ai_agreement", resolved[0].decision_source)

    def test_adjudication_cannot_choose_an_unrelated_third_item(self) -> None:
        case = ai_case("guard-third", 80_000_000, weak=True, anomaly=True)
        ai_result = AIResult(case.task.task_id, "guard-third", "CFI-05", "首次判断为投资", "high")
        adjudication = AIResult("ADJ-THIRD", "guard-third", "CFF-01", "改判筹资", "high")
        resolved = resolve_automatic_decisions([case.decision], [ai_result], [adjudication], ITEM_NAMES)
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
        resolved = resolve_automatic_decisions([case.decision], [ai_result], [adjudication], ITEM_NAMES)
        self.assertFalse(resolved[0].resolved)
        self.assertEqual("CFO-03", resolved[0].system_item_id)

    def test_unresolved_below_performance_never_goes_to_human(self) -> None:
        unresolved = [ai_case("small", 74_999_999, weak=True, anomaly=True).unresolved]
        self.assertEqual((), build_review_batches(unresolved, performance_cent=75_000_000))

    def test_material_unresolved_without_real_alternative_is_rejected(self) -> None:
        unresolved = ai_case(
            "no-alternative",
            80_000_000,
            weak=True,
            anomaly=True,
            alternatives=(),
        ).unresolved

        with self.assertRaisesRegex(ValueError, "没有可供人工选择的备选现流项目"):
            build_review_batches([unresolved], performance_cent=75_000_000)

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

    def test_material_consistency_group_keeps_each_real_component_in_human_review(self) -> None:
        first = replace(
            ai_case("group-a", 40_000_000, True, True).unresolved,
            group_impact_cent=80_000_000,
        )
        second = replace(
            ai_case(
                "group-b",
                40_000_000,
                True,
                True,
                alternatives=("CFO-03", "CFI-01"),
            ).unresolved,
            system_item_id="CFI-01",
            group_impact_cent=80_000_000,
        )

        batches = build_review_batches((first, second), 75_000_000)

        self.assertEqual(2, len(batches))
        self.assertEqual({"group-a", "group-b"}, {item.component_ids[0] for item in batches})

    def test_review_pattern_keeps_full_business_text_except_dates_and_numbers(self) -> None:
        first = _review_text_pattern("支付甲公司服务费 2026-01-01 1,000元")
        second = _review_text_pattern("支付乙公司服务费 2026-02-02 2,000元")
        self.assertNotEqual(first, second)
        self.assertNotIn("2026", first)
        self.assertNotIn("1000", first)


    def test_label_rule_conflict_joins_ai_regardless_of_performance(self) -> None:
        # 复核路由：保留原标签的冲突（40–69 分档），达明显微小临界值即送 AI，不受实际执行门槛限制
        from cashflow_direct.models import ClassificationDecision
        from tests.fixture_factory import cashflow_component

        component = cashflow_component("税收滞纳金", -6_000_000, ("营业外支出",),
                                       original_item_text="支付的各项税费", component_id="CFLX")
        decision = ClassificationDecision(
            component_id="CFLX", system_item_id="CFO-06", system_item_name="支付的各项税费",
            normal_direction="outflow", matched_rule_id="LABEL-KEPT-INSUFFICIENT-EVIDENCE",
            reason="冲突", evidence_level="medium", resolved=False,
            evidence_score=55, label_kept=True,
        )
        tasks = select_ai_tasks([component], [decision], self.materiality)
        self.assertEqual(["CFLX"], [task.component_id for task in tasks])

    def test_label_rule_conflict_below_trivial_is_not_sent(self) -> None:
        # 复核路由：冲突 40–69 分档但金额低于明显微小临界值，且同类累计不足 → 不送 AI
        from cashflow_direct.models import ClassificationDecision
        from tests.fixture_factory import cashflow_component

        component = cashflow_component("税收滞纳金", -4_999_999, ("营业外支出",),
                                       original_item_text="支付的各项税费", component_id="CFLY")
        decision = ClassificationDecision(
            component_id="CFLY", system_item_id="CFO-06", system_item_name="支付的各项税费",
            normal_direction="outflow", matched_rule_id="LABEL-KEPT-INSUFFICIENT-EVIDENCE",
            reason="冲突", evidence_level="medium", resolved=False,
            evidence_score=55, label_kept=True,
        )
        tasks = select_ai_tasks([component], [decision], self.materiality)
        self.assertEqual((), tasks)

    def test_overall_materiality_items_skip_ai_review(self) -> None:
        # 达到整体重要性的事项不送 AI，留给强制人工复核（Task 7）
        component = cashflow_component("大额往来", 100_000_000, component_id="BIG")
        decision = ClassificationDecision(
            component.component_id, "CFO-03", "收到其他与经营活动有关的现金", "inflow",
            "CFO-03-CURRENT", "暂定分类", "low", evidence_score=10, resolved=True,
        )
        tasks = select_ai_tasks([component], [decision], self.materiality)
        self.assertEqual([], list(tasks))

    def test_score_below_70_with_performance_amount_goes_to_ai(self) -> None:
        # 打分不足 70 且金额达实际执行重要性 → 送 AI；打满 70 → 不送（Task 7）
        materiality = MaterialityAmounts(10_000_000, 5_000_000, 500_000)
        below = cashflow_component("往来款", 5_000_000, component_id="SCORE-69")
        above = cashflow_component("往来款", 5_000_000, component_id="SCORE-70")
        decisions = [
            ClassificationDecision(
                component.component_id, "CFO-03", "收到其他与经营活动有关的现金", "inflow",
                "CFO-03-CURRENT", "暂定分类", "medium", evidence_score=score,
            )
            for component, score in ((below, 69), (above, 70))
        ]
        tasks = select_ai_tasks([below, above], decisions, materiality)
        self.assertEqual(["SCORE-69"], [task.component_id for task in tasks])

    def test_overall_materiality_forces_mandatory_review_batch(self) -> None:
        # 达整体重要性的事项生成强制人工复核批次，无备选也不报错（Task 8）
        from cashflow_direct.materiality import build_review_batches
        from cashflow_direct.models import UnresolvedDecision

        item = UnresolvedDecision(
            component_id="CMP-BIG", cash_delta_cent=-240333845, cash_direction="outflow",
            original_item="支付的各项税费", system_item_id="CFO-06",
            adjudication_status="达到财务报表整体重要性，强制人工复核",
            counterpart_group="应交税费", summary_pattern="留抵退税缴回",
            alternative_item_ids=(), reason="测试", mandatory=True,
        )
        batches = build_review_batches((item,), performance_cent=50000000)
        self.assertEqual(1, len(batches))
        self.assertTrue(batches[0].mandatory)
        self.assertEqual((), batches[0].alternative_item_codes)

    def test_basis_gate_accepts_traceable_basis(self) -> None:
        # 复核修复：四类可追查依据（准则条款/应用指南章节或引文/知识库位置/NOTE编号）均应通过
        self.assertIsNone(validate_basis_text("依据企业会计准则第31号第十条第（一）项"))
        self.assertIsNone(validate_basis_text("依据企业会计准则第31号第十项"))
        self.assertIsNone(validate_basis_text("应用指南第三十二章'销售商品、提供劳务收到的现金'"))
        self.assertIsNone(validate_basis_text("知识库第433行：代扣代缴的个人所得税款"))
        self.assertIsNone(validate_basis_text("依据公司特殊规则：NOTE-01"))

    def test_basis_gate_rejects_vague_basis(self) -> None:
        # 复核修复：空泛理由一律拒收
        for text in ("根据准则", "综合判断", "摘要支持", "根据业务实质", ""):
            with self.subTest(text=text):
                self.assertIsNotNone(validate_basis_text(text))

    def test_vague_reason_result_is_rejected_by_validation(self) -> None:
        # 复核修复：AI 结果理由过不了依据门禁 → 判为无效，不进入后续环节
        tasks = tuple(
            ai_case(str(index), 80_000_000, weak=True, anomaly=False).task for index in range(2)
        )
        payloads = [
            {"task_id": tasks[0].task_id, "component_id": "0", "item_id": "CFO-03",
             "reason": "根据准则", "confidence": "high"},
            {"task_id": tasks[1].task_id, "component_id": "1", "item_id": "CFO-03",
             "reason": "知识库第12行：往来款", "confidence": "high"},
        ]
        validation = validate_ai_results(tasks, payloads, {"CFO-03"})
        self.assertEqual([tasks[1].task_id], [item.task_id for item in validation.valid_results])
        self.assertEqual((tasks[0].task_id,), validation.invalid_ids)

    def test_adjudication_override_clears_evidence_and_uses_real_item_name(self) -> None:
        # 复核修复：AI 裁决改判后旧规则证据清零，项目名称用真实项目名而非编号
        case = ai_case("override", 80_000_000, weak=False, anomaly=True)
        ai_result = AIResult(case.task.task_id, "override", "CFI-05", "知识库第5行：投资流出", "medium")
        adjudicated = AIResult("ADJ-OV", "override", "CFI-05", "知识库第5行：投资流出", "high")
        resolved = resolve_automatic_decisions(
            [case.decision], [ai_result], [adjudicated], ITEM_NAMES
        )
        self.assertTrue(resolved[0].resolved)
        self.assertEqual("CFI-05", resolved[0].system_item_id)
        self.assertEqual("支付其他与投资活动有关的现金", resolved[0].system_item_name)
        self.assertEqual(0, resolved[0].evidence_score)
        self.assertEqual((), resolved[0].evidence_sources)

    def test_business_rule_conflict_any_score_trivial_goes_ai(self) -> None:
        # 复核修复：业务规则冲突不分评分档位，达明显微小临界值即送 AI（如 70 对 70 混合缴款）
        case = _routing_case("CONF-70", -100_000, score=70,
                             rule_id="BUSINESS-RULE-CONFLICT")
        materiality = MaterialityAmounts(10_000_000, 1_000_000, 100_000)
        tasks = select_ai_tasks((case[0],), (case[1],), materiality)
        self.assertEqual(["CONF-70"], [task.component_id for task in tasks])

    def test_label_conflict_40_to_69_trivial_goes_ai(self) -> None:
        # 复核修复：标签留名冲突的复核路由——40-69 分档达明显微小临界值即送 AI
        at = _routing_case("AT", -100_000, score=55, label_kept=True)
        below = _routing_case("BELOW", -99_999, score=55, label_kept=True)
        materiality = MaterialityAmounts(10_000_000, 1_000_000, 100_000)
        tasks = select_ai_tasks(
            (at[0], below[0]), (at[1], below[1]), materiality)
        self.assertEqual(["AT"], [task.component_id for task in tasks])

    def test_label_conflict_below_40_needs_performance(self) -> None:
        # 复核修复：标签留名冲突不足 40 分，走更严的实际执行重要性门槛
        at = _routing_case("AT", -1_000_000, score=30, label_kept=True)
        below = _routing_case("BELOW", -999_999, score=30, label_kept=True)
        materiality = MaterialityAmounts(10_000_000, 1_000_000, 100_000)
        tasks = select_ai_tasks(
            (at[0], below[0]), (at[1], below[1]), materiality)
        self.assertEqual(["AT"], [task.component_id for task in tasks])

    def test_pooled_same_type_cumulative_goes_ai(self) -> None:
        # 复核修复：同类小额低分凑够实际执行门槛整组送 AI，上下文附累计口径说明
        cases = tuple(
            _routing_case(f"POOL-{index}", -400_000, score=30, label_kept=True)
            for index in range(3)
        )
        materiality = MaterialityAmounts(10_000_000, 1_000_000, 100_000)
        tasks = select_ai_tasks(
            tuple(item[0] for item in cases),
            tuple(item[1] for item in cases), materiality)
        self.assertEqual(
            ["POOL-0", "POOL-1", "POOL-2"],
            sorted(task.component_id for task in tasks))
        for task in tasks:
            self.assertIn("同类 3 笔", task.context)
            self.assertIn("累计金额 12,000.00 元", task.context)

    def test_below_trivial_and_pool_short_stays_trace_only(self) -> None:
        # 复核修复：低分且低于明显微小临界值、同类累计也凑不够的，留台账留痕不送 AI
        case = _routing_case("SMALL", -50_000, score=30, label_kept=True)
        materiality = MaterialityAmounts(10_000_000, 1_000_000, 100_000)
        tasks = select_ai_tasks((case[0],), (case[1],), materiality)
        self.assertEqual((), tasks)

    def test_plain_weak_needs_performance(self) -> None:
        # 复核修复：普通低证据（非冲突、非异常）达实际执行重要性才送 AI
        at = _routing_case("AT", -1_000_000, score=10, weak=True,
                           rule_id="CFO-04-PURCHASE")
        below = _routing_case("BELOW", -999_999, score=10, weak=True,
                              rule_id="CFO-04-PURCHASE")
        materiality = MaterialityAmounts(10_000_000, 1_000_000, 100_000)
        tasks = select_ai_tasks(
            (at[0], below[0]), (at[1], below[1]), materiality)
        self.assertEqual(["AT"], [task.component_id for task in tasks])

    def test_overall_materiality_never_goes_ai(self) -> None:
        # 复核修复：达到财务报表整体重要性的，一律强制人工复核，冲突加异常叠加也不送 AI
        case = _routing_case("BIG", -10_000_000, score=55, label_kept=True,
                             anomaly=True)
        materiality = MaterialityAmounts(10_000_000, 1_000_000, 100_000)
        tasks = select_ai_tasks((case[0],), (case[1],), materiality)
        self.assertEqual((), tasks)

    def test_mandatory_batch_offers_all_leaf_items(self) -> None:
        # 复核修复：强制人工复核批次的可改选范围 = 除原判外的全部叶子标准项目
        rules = load_rule_pack(ROOT)
        leaf_ids = tuple(
            item.item_id for item in rules.statement_items if item.is_leaf)
        item = UnresolvedDecision(
            component_id="CMP-BIG",
            cash_delta_cent=-240333845,
            cash_direction="outflow",
            original_item="支付的各项税费",
            system_item_id="CFO-06",
            adjudication_status="达到财务报表整体重要性，强制人工复核",
            counterpart_group="应交税费",
            summary_pattern="留抵退税缴回",
            alternative_item_ids=(),
            reason="留抵退税缴回计入支付税费",
            mandatory=True,
        )
        batches = build_review_batches(
            (item,), 1_000_000, all_leaf_item_ids=leaf_ids)
        self.assertEqual(1, len(batches))
        self.assertEqual(len(leaf_ids) - 1, len(batches[0].alternative_item_codes))
        self.assertNotIn("CFO-06", batches[0].alternative_item_codes)


if __name__ == "__main__":
    unittest.main()
