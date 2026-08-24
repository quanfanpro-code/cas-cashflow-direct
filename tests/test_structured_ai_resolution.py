from __future__ import annotations

import pytest

from cashflow_direct.ai_review import (
    AISourceReview,
    StructuredAIResult,
    resolve_structured_ai_results,
)
from cashflow_direct.decision_policy import EvidenceQuality
from cashflow_direct.models import AITask, ClassificationDecision


ITEM_NAMES = {
    "CFO-01": "销售商品、提供劳务收到的现金",
    "CFO-03": "收到其他与经营活动有关的现金",
    "CFO-05": "收到的税费返还",
}
ITEM_DIRECTIONS = {"CFO-01": "inflow", "CFO-03": "inflow", "CFO-05": "inflow"}


def _decision(
    *,
    original_state: str,
    materiality: str,
    action: str,
    candidate: str = "CFO-01",
    source_conflict: bool = False,
    original_standard_item_id: str = "",
    review_policy: str = "",
) -> ClassificationDecision:
    return ClassificationDecision(
        component_id="CMP-1",
        system_item_id=candidate,
        system_item_name=ITEM_NAMES[candidate],
        normal_direction="inflow",
        matched_rule_id="TEST",
        reason="复核前候选",
        evidence_level="medium",
        resolved=False,
        evidence_score=None if source_conflict else 35,
        candidate_item_ids=("CFO-01", "CFO-03", "CFO-05"),
        original_item_state=original_state,
        source_conflict=source_conflict,
        decision_action=action,
        materiality_level=materiality,
        candidate_status="source_conflict" if source_conflict else "available",
        original_standard_item_id=original_standard_item_id,
        ai_review_policy=review_policy,
    )


def _task(task_id: str) -> AITask:
    return AITask(
        task_id=task_id,
        component_id="CMP-1",
        context="摘要原文：销售回款；完整对方科目路径：合同负债",
        original_item="",
        system_item_id="CFO-01",
        rule_evidence="系统候选仅供复核",
        candidate_item_ids=("CFO-01", "CFO-03"),
    )


def _result(
    task_id: str,
    *,
    candidate: str = "CFO-01",
    summary_quality: EvidenceQuality = EvidenceQuality.STRONG,
    account_quality: EvidenceQuality = EvidenceQuality.MEDIUM,
    conflict: bool = False,
    business_conflict: bool = False,
    direction_status: str = "compatible",
    sources_independent: bool = True,
) -> StructuredAIResult:
    return StructuredAIResult(
        task_id=task_id,
        component_id="CMP-1",
        summary=AISourceReview(
            candidate,
            summary_quality,
            "销售回款",
            ("action:sale_collection",),
            conflict,
        ),
        account_path=AISourceReview(
            "CFO-03" if conflict else candidate,
            account_quality,
            "合同负债",
            ("account:contract_liability",),
            conflict,
        ),
        sources_independent=sources_independent,
        business_conflict=business_conflict,
        direction_status=direction_status,
        reason="仅重新解释两个原始来源",
        alternative_item_ids=(),
        note_ids=(),
    )


def test_single_ai_can_change_only_when_recalculated_score_reaches_70() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M1",
        action="ai_review",
        original_standard_item_id="CFO-03",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (_result("AI-1"),),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_change"
    assert resolved.evidence_score == 70
    assert resolved.decision_source == "ai_reviewed_system_decision"


def test_single_ai_score_70_keeps_original_when_customer_selected_90() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M1",
        action="ai_review",
        original_standard_item_id="CFO-03",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (_result("AI-1"),),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
        automatic_change_threshold=90,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_keep"
    assert resolved.system_item_id == "CFO-03"


def test_ai_resolution_rejects_invalid_customer_threshold_before_routing() -> None:
    decision = _decision(
        original_state="agrees",
        materiality="M0",
        action="automatic_keep",
        original_standard_item_id="CFO-01",
    )

    with pytest.raises(ValueError, match="只允许50、55、70、90"):
        resolve_structured_ai_results(
            (decision,),
            (),
            (),
            ITEM_NAMES,
            ITEM_DIRECTIONS,
            automatic_change_threshold=60,
        )


def test_single_ai_below_change_threshold_restores_valid_original() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M1",
        action="ai_review",
        original_standard_item_id="CFO-03",
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (
            _result(
                "AI-1",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is True
    assert unresolved.decision_action == "automatic_keep"
    assert unresolved.evidence_score == 35
    assert unresolved.system_item_id == "CFO-03"


def test_single_ai_changed_candidate_does_not_prove_change_and_keeps_original() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M1",
        action="ai_review",
        candidate="CFO-01",
        original_standard_item_id="CFO-05",
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (_result("AI-1", candidate="CFO-03"),),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is True
    assert unresolved.decision_action == "automatic_keep"
    assert unresolved.system_item_id == "CFO-05"
    assert unresolved.evidence_score == 70


def test_valid_original_ai_can_confirm_no_modification_without_naming_a_candidate() -> None:
    decision = _decision(
        original_state="agrees",
        materiality="M2",
        action="ai_review",
        original_standard_item_id="CFO-01",
    )
    result = StructuredAIResult(
        task_id="AI-1",
        component_id="CMP-1",
        summary=AISourceReview(
            "", EvidenceQuality.INVALID, "摘要不足以证明需要修改", (), False
        ),
        account_path=AISourceReview(
            "", EvidenceQuality.INVALID, "路径不足以证明需要修改", (), False
        ),
        sources_independent=False,
        business_conflict=False,
        direction_status="compatible",
        reason="未取得足以修改原项目的证据",
        alternative_item_ids=(),
        note_ids=(),
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (result,),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_keep"
    assert resolved.system_item_id == "CFO-01"


def test_single_ai_technical_failure_uses_the_current_table_failure_exit() -> None:
    valid_original = _decision(
        original_state="conflicts",
        materiality="M1",
        action="ai_review",
        original_standard_item_id="CFO-03",
        review_policy="valid_original_change",
    )
    blank_high = _decision(
        original_state="blank",
        materiality="M1",
        action="ai_review",
        review_policy="blank_90_single",
    )

    kept = resolve_structured_ai_results(
        (valid_original,),
        (_task("AI-1"),),
        (),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
        failed_task_ids={"AI-1"},
    )[0]
    escalated = resolve_structured_ai_results(
        (blank_high,),
        (_task("AI-1"),),
        (),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
        failed_task_ids={"AI-1"},
    )[0]

    assert kept.decision_action == "automatic_keep"
    assert kept.resolved is True
    assert kept.system_item_id == "CFO-03"
    assert escalated.decision_action == "double_ai_review"
    assert escalated.resolved is False


def test_two_blind_ai_reviews_must_agree_and_are_not_added_together() -> None:
    decision = _decision(
        original_state="blank", materiality="M2", action="double_ai_review"
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A"), _result("AI-B")),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_fill"
    assert resolved.evidence_score == 70


def test_two_blind_ai_reviews_disagree_then_request_third_review() -> None:
    decision = _decision(
        original_state="blank", materiality="M2", action="double_ai_review"
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A"), _result("AI-B", candidate="CFO-03")),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is False
    assert unresolved.decision_action == "ai_third_review"
    assert unresolved.evidence_score is None


def test_individual_tax_service_blind_reviews_reenter_normal_table() -> None:
    blank = _decision(
        original_state="blank",
        materiality="M2",
        action="double_ai_review",
        review_policy="individual_tax_service",
    )
    valid_original = _decision(
        original_state="conflicts",
        materiality="M2",
        action="double_ai_review",
        candidate="CFO-03",
        original_standard_item_id="CFO-01",
        review_policy="individual_tax_service",
    )

    blank_result = resolve_structured_ai_results(
        (blank,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A"), _result("AI-B")),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]
    changed_result = resolve_structured_ai_results(
        (valid_original,),
        (_task("AI-A"), _task("AI-B")),
        (
            _result("AI-A", candidate="CFO-03"),
            _result(
                "AI-B",
                candidate="CFO-03",
                account_quality=EvidenceQuality.STRONG,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert blank_result.decision_action == "automatic_fill"
    assert blank_result.system_item_id == "CFO-01"
    assert changed_result.decision_action == "automatic_change"
    assert changed_result.system_item_id == "CFO-03"


def test_individual_tax_service_blind_reviews_without_one_answer_go_to_human() -> None:
    decision = _decision(
        original_state="blank",
        materiality="M2",
        action="double_ai_review",
        review_policy="individual_tax_service",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A"), _result("AI-B", candidate="CFO-03")),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.decision_action == "human_decision"
    assert resolved.resolved is False


def test_two_blind_ai_reviews_same_candidate_can_confirm_even_if_quality_differs() -> None:
    decision = _decision(
        original_state="blank", materiality="M2", action="double_ai_review"
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (
            _result("AI-A"),
            _result("AI-B", account_quality=EvidenceQuality.STRONG),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is True
    assert unresolved.decision_action == "automatic_fill"
    assert unresolved.evidence_score == 70


def test_two_blind_ai_reviews_with_different_independence_go_to_human() -> None:
    decision = _decision(
        original_state="agrees",
        materiality="M2",
        action="double_ai_review",
        original_standard_item_id="CFO-01",
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (
            _result("AI-A"),
            _result("AI-B", sources_independent=False),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is True
    assert unresolved.decision_action == "automatic_keep"
    assert unresolved.evidence_score is None
    assert unresolved.system_item_id == "CFO-01"


def test_source_conflict_remaining_after_review_restores_valid_original() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M0",
        action="ai_review",
        source_conflict=True,
        original_standard_item_id="CFO-03",
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (_result("AI-1", conflict=True),),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is True
    assert unresolved.decision_action == "automatic_keep"
    assert unresolved.evidence_score is None
    assert unresolved.system_item_id == "CFO-03"


def test_blank_low_score_single_ai_can_fill_without_score_uplift() -> None:
    decision = _decision(
        original_state="blank", materiality="M0", action="ai_review"
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (
            _result(
                "AI-1",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_fill"
    assert resolved.system_item_id == "CFO-01"
    assert resolved.evidence_score == 35


def test_blank_low_score_two_ai_agreement_can_fill_without_score_uplift() -> None:
    decision = _decision(
        original_state="blank", materiality="M1", action="double_ai_review"
    )
    results = tuple(
        _result(
            task_id,
            summary_quality=EvidenceQuality.MEDIUM,
            account_quality=EvidenceQuality.WEAK,
        )
        for task_id in ("AI-A", "AI-B")
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        results,
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_fill"
    assert resolved.evidence_score == 35


def test_blank_low_score_two_ai_disagreement_requests_third_independent_review() -> None:
    decision = _decision(
        original_state="blank", materiality="M1", action="double_ai_review"
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (
            _result(
                "AI-A",
                candidate="CFO-01",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
            _result(
                "AI-B",
                candidate="CFO-03",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is False
    assert unresolved.decision_action == "ai_third_review"


def test_blank_low_score_one_failed_blind_review_still_requests_third_review() -> None:
    decision = _decision(
        original_state="blank", materiality="M1", action="double_ai_review"
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A"),),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
        failed_task_ids={"AI-B"},
    )[0]

    assert unresolved.decision_action == "ai_third_review"
    assert unresolved.resolved is False


def test_blank_low_score_three_ai_majority_fills_without_score_uplift() -> None:
    decision = _decision(
        original_state="blank", materiality="M1", action="ai_third_review"
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B"), _task("AI-C")),
        (
            _result(
                "AI-A",
                candidate="CFO-01",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
            _result(
                "AI-B",
                candidate="CFO-03",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
            _result(
                "AI-C",
                candidate="CFO-01",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_fill"
    assert resolved.system_item_id == "CFO-01"
    assert resolved.evidence_score == 35


def test_blank_low_score_three_distinct_ai_candidates_go_to_human() -> None:
    decision = _decision(
        original_state="blank", materiality="M1", action="ai_third_review"
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B"), _task("AI-C")),
        (
            _result("AI-A", candidate="CFO-01"),
            _result("AI-B", candidate="CFO-03"),
            _result("AI-C", candidate="CFO-05"),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is False
    assert unresolved.decision_action == "human_decision"


def test_third_ai_technical_failure_is_not_a_vote() -> None:
    decision = _decision(
        original_state="blank", materiality="M1", action="ai_third_review"
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B"), _task("AI-C")),
        (
            _result("AI-A", candidate="CFO-01"),
            _result("AI-B", candidate="CFO-03"),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
        failed_task_ids={"AI-C"},
    )[0]

    assert unresolved.decision_action == "human_decision"
    assert unresolved.resolved is False


def test_direction_check_technical_failure_uses_amount_specific_exit() -> None:
    low = _decision(
        original_state="blank",
        materiality="M0",
        action="ai_review",
        review_policy="direction_compatibility",
    )
    middle = _decision(
        original_state="blank",
        materiality="M1",
        action="ai_review",
        review_policy="direction_compatibility",
    )

    low_result = resolve_structured_ai_results(
        (low,),
        (_task("AI-1"),),
        (),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
        failed_task_ids={"AI-1"},
    )[0]
    middle_result = resolve_structured_ai_results(
        (middle,),
        (_task("AI-1"),),
        (),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
        failed_task_ids={"AI-1"},
    )[0]

    assert low_result.decision_action == "low_amount_human_batch"
    assert middle_result.decision_action == "human_decision"


def test_direction_check_valid_blind_consensus_can_fill_a_blank_project() -> None:
    decision = _decision(
        original_state="blank",
        materiality="M2",
        action="double_ai_review",
        review_policy="direction_compatibility",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A"), _result("AI-B")),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.decision_action == "automatic_fill"
    assert resolved.system_item_id == "CFO-01"


def test_valid_original_low_score_ai_change_claim_requests_blind_double_followup() -> None:
    decision = _decision(
        original_state="agrees",
        materiality="M2",
        action="ai_review",
        candidate="CFO-01",
        original_standard_item_id="CFO-01",
    )

    unresolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (
            _result(
                "AI-1",
                candidate="CFO-03",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.WEAK,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert unresolved.resolved is False
    assert unresolved.decision_action == "ai_double_followup_review"


def test_valid_original_blind_double_followup_changes_only_at_70() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M2",
        action="ai_double_followup_review",
        candidate="CFO-03",
        original_standard_item_id="CFO-01",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A", candidate="CFO-03"), _result("AI-B", candidate="CFO-03")),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_change"
    assert resolved.system_item_id == "CFO-03"
    assert resolved.evidence_score == 70


def test_valid_original_blind_double_followup_accepts_same_candidate_at_70_and_90() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M2",
        action="ai_double_followup_review",
        candidate="CFO-03",
        original_standard_item_id="CFO-01",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (
            _result("AI-A", candidate="CFO-03"),
            _result(
                "AI-B",
                candidate="CFO-03",
                account_quality=EvidenceQuality.STRONG,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_change"
    assert resolved.system_item_id == "CFO-03"
    assert resolved.evidence_score == 70


def test_valid_original_m2_change_reviews_accept_same_candidate_at_70_and_90() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M2",
        action="double_ai_review",
        candidate="CFO-03",
        original_standard_item_id="CFO-01",
        review_policy="valid_original_change",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (
            _result("AI-A", candidate="CFO-03"),
            _result(
                "AI-B",
                candidate="CFO-03",
                account_quality=EvidenceQuality.STRONG,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_change"
    assert resolved.system_item_id == "CFO-03"
    assert resolved.evidence_score == 70


def test_valid_original_blind_double_followup_disagreement_keeps_original() -> None:
    decision = _decision(
        original_state="conflicts",
        materiality="M2",
        action="ai_double_followup_review",
        candidate="CFO-03",
        original_standard_item_id="CFO-01",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-A"), _task("AI-B")),
        (_result("AI-A", candidate="CFO-03"), _result("AI-B", candidate="CFO-05")),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_keep"
    assert resolved.system_item_id == "CFO-01"


def test_resolved_source_conflict_recompares_the_original_item() -> None:
    decision = _decision(
        original_state="pending_comparison",
        materiality="M1",
        action="ai_review",
        source_conflict=True,
        original_standard_item_id="CFO-01",
    )

    resolved = resolve_structured_ai_results(
        (decision,),
        (_task("AI-1"),),
        (
            _result(
                "AI-1",
                summary_quality=EvidenceQuality.MEDIUM,
                account_quality=EvidenceQuality.MEDIUM,
            ),
        ),
        ITEM_NAMES,
        ITEM_DIRECTIONS,
    )[0]

    assert resolved.original_item_state == "agrees"
    assert resolved.evidence_score == 50
    assert resolved.resolved is True
    assert resolved.decision_action == "automatic_keep"
