from __future__ import annotations

from dataclasses import replace

import pytest

from cashflow_direct.blank_original_fallback import apply_blank_original_fallback
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    MaterialityAmounts,
)


ORDERED_ITEMS = ("CFO-01", "CFO-03", "CFO-04", "CFO-07", "CFI-01")
DIRECTIONS = {
    "CFO-01": "inflow",
    "CFO-03": "inflow",
    "CFO-04": "outflow",
    "CFO-07": "outflow",
    "CFI-01": "inflow",
}


def component(amount_cent: int = -99_999) -> CashflowComponent:
    return CashflowComponent(
        component_id="C1",
        voucher_key="V1",
        summary="测试业务",
        cash_delta_cent=amount_cent,
        original_item_text="",
    )


def decision(**changes: object) -> ClassificationDecision:
    base = ClassificationDecision(
        component_id="C1",
        system_item_id="",
        system_item_name="",
        normal_direction="net",
        matched_rule_id="TEST",
        reason="既有判断仍未取得唯一项目",
        evidence_level="weak",
        resolved=False,
        evidence_score=35,
        candidate_item_ids=("CFO-04", "CFO-07"),
        summary_candidate_item_ids=("CFO-07",),
        account_path_candidate_item_ids=("CFO-04",),
        original_item_state="blank",
        summary_quality=10,
        account_path_quality=25,
        source_conflict=True,
        decision_action="human_decision",
        candidate_status="ambiguous",
    )
    return replace(base, **changes)


def apply(
    current: ClassificationDecision,
    *,
    amount_cent: int = -99_999,
    performance_cent: int = 100_000,
) -> ClassificationDecision:
    return apply_blank_original_fallback(
        component(amount_cent),
        current,
        MaterialityAmounts(
            overall_cent=performance_cent * 2,
            performance_cent=performance_cent,
            trivial_cent=max(1, performance_cent // 10),
        ),
        ORDERED_ITEMS,
        DIRECTIONS,
    )


def test_higher_account_path_score_wins_without_inflating_score() -> None:
    original = decision()

    result = apply(original)

    assert result.resolved is True
    assert result.system_item_id == "CFO-04"
    assert result.decision_action == "automatic_fill"
    assert result.fallback_source == "account_path"
    assert result.evidence_score == original.evidence_score
    assert result.source_conflict is True


def test_higher_summary_score_wins() -> None:
    result = apply(
        decision(
            summary_quality=45,
            account_path_quality=25,
            summary_candidate_item_ids=("CFO-07",),
            account_path_candidate_item_ids=("CFO-04",),
        )
    )

    assert result.system_item_id == "CFO-07"
    assert result.fallback_source == "summary"


def test_equal_scores_same_item_use_the_common_item() -> None:
    result = apply(
        decision(
            summary_quality=25,
            account_path_quality=25,
            summary_candidate_item_ids=("CFO-04",),
            account_path_candidate_item_ids=("CFO-04",),
        )
    )

    assert result.system_item_id == "CFO-04"
    assert result.fallback_source == "account_path"


def test_equal_scores_different_items_prefer_account_path() -> None:
    result = apply(
        decision(
            summary_quality=25,
            account_path_quality=25,
            summary_candidate_item_ids=("CFO-07",),
            account_path_candidate_item_ids=("CFO-04",),
        )
    )

    assert result.system_item_id == "CFO-04"
    assert result.fallback_source == "account_path"


def test_multiple_candidates_use_source_preference_then_statement_order() -> None:
    preferred = apply(
        decision(
            account_path_candidate_item_ids=("CFO-07", "CFO-04"),
            account_path_preferred_item_id="CFO-07",
        )
    )
    ordered = apply(
        decision(
            system_item_id="",
            account_path_candidate_item_ids=("CFO-07", "CFO-04"),
            account_path_preferred_item_id="",
        )
    )

    assert preferred.system_item_id == "CFO-07"
    assert preferred.fallback_step == "source_preferred"
    assert ordered.system_item_id == "CFO-04"
    assert ordered.fallback_step == "statement_order"


@pytest.mark.parametrize(
    ("amount_cent", "expected_item"),
    ((99_999, "CFO-03"), (-99_999, "CFO-07")),
)
def test_no_candidates_fall_back_to_other_operating_by_cash_direction(
    amount_cent: int,
    expected_item: str,
) -> None:
    result = apply(
        decision(
            candidate_item_ids=(),
            summary_candidate_item_ids=(),
            account_path_candidate_item_ids=(),
            summary_quality=0,
            account_path_quality=0,
            evidence_score=0,
            source_conflict=False,
            candidate_status="no_candidate",
        ),
        amount_cent=amount_cent,
    )

    assert result.system_item_id == expected_item
    assert result.fallback_source == "cash_direction"
    assert result.fallback_step == "direction_other_operating"
    assert result.evidence_score == 0


def test_blank_original_invalid_input_below_performance_still_gets_cash_direction_fallback() -> None:
    result = apply(
        decision(
            decision_action="isolate_invalid_input",
            candidate_status="invalid_input",
            candidate_item_ids=(),
            summary_candidate_item_ids=(),
            account_path_candidate_item_ids=(),
            summary_quality=0,
            account_path_quality=0,
            evidence_score=0,
        )
    )

    assert result.resolved is True
    assert result.system_item_id == "CFO-07"
    assert result.decision_action == "automatic_fill"
    assert result.candidate_status == "invalid_input"
    assert result.reason.startswith("既有判断仍未取得唯一项目")


@pytest.mark.parametrize(
    ("amount_cent", "performance_cent", "should_resolve"),
    ((99_999, 100_000, True), (100_000, 100_000, False), (199_999, 200_000, True)),
)
def test_performance_materiality_uses_runtime_amount(
    amount_cent: int,
    performance_cent: int,
    should_resolve: bool,
) -> None:
    result = apply(
        decision(summary_candidate_item_ids=("CFO-03",), summary_quality=45),
        amount_cent=amount_cent,
        performance_cent=performance_cent,
    )

    assert result.resolved is should_resolve


@pytest.mark.parametrize(
    "current",
    (
        decision(original_item_state="agrees"),
        decision(direction_status="unknown"),
        decision(resolved=True),
    ),
)
def test_inapplicable_decisions_remain_unchanged(
    current: ClassificationDecision,
) -> None:
    assert apply(current) == current
