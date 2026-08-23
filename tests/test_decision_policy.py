from __future__ import annotations

import importlib
import unittest

import pytest

from cashflow_direct.models import MaterialityAmounts


class AnomalyClueRoutingTests(unittest.TestCase):
    def test_direction_clue_cannot_displace_valid_original_without_change_evidence(self) -> None:
        policy = _policy()
        route = policy.route_decision(
            score=45,
            original_state=policy.OriginalItemState.CONFLICTS,
            materiality=policy.MaterialityLevel.M0,
            direction_status="incompatible",
        )

        self.assertIs(policy.DecisionAction.AUTOMATIC_KEEP, route.action)
        self.assertEqual("", route.forced_check)

    def test_direction_clue_cannot_block_a_change_supported_by_two_sources(self) -> None:
        policy = _policy()
        route = policy.route_decision(
            score=70,
            original_state=policy.OriginalItemState.CONFLICTS,
            materiality=policy.MaterialityLevel.M0,
            direction_status="incompatible",
        )

        self.assertIs(policy.DecisionAction.AUTOMATIC_CHANGE, route.action)
        self.assertEqual("", route.forced_check)


def _policy():
    return importlib.import_module("cashflow_direct.decision_policy")


def _source(source: str, item_id: str, quality: int, *facts: str):
    policy = _policy()
    return policy.EvidenceSourceAssessment(
        source=policy.EvidenceSource(source),
        candidate_item_id="" if quality == 0 else item_id,
        quality=policy.EvidenceQuality(quality),
        basis_text="构造测试原文",
        classification_facts=() if quality == 0 else tuple(facts),
    )


def test_classification_action_table_has_no_legacy_serial_second_ai_action() -> None:
    policy = _policy()

    with pytest.raises(ValueError):
        policy.DecisionAction("ai_second_review")


def test_four_quality_levels_have_the_approved_values() -> None:
    policy = _policy()

    assert [(item.name, item.value) for item in policy.EvidenceQuality] == [
        ("INVALID", 0),
        ("WEAK", 10),
        ("MEDIUM", 25),
        ("STRONG", 45),
    ]


@pytest.mark.parametrize(
    ("summary_quality", "path_quality", "expected_score"),
    [
        (0, 0, 0),
        (0, 10, 10),
        (10, 10, 20),
        (0, 25, 25),
        (10, 25, 35),
        (0, 45, 45),
        (25, 25, 50),
        (10, 45, 55),
        (25, 45, 70),
        (45, 45, 90),
    ],
)
def test_only_the_ten_approved_scores_are_produced(
    summary_quality: int,
    path_quality: int,
    expected_score: int,
) -> None:
    policy = _policy()
    summary = _source("summary", "CFO-01", summary_quality, "action:salary")
    path = _source("account_path", "CFO-01", path_quality, "account:employee_benefit")

    result = policy.combine_source_assessments(summary, path)

    assert result.score == expected_score
    assert result.conflict is False


def test_single_source_never_scores_more_than_45() -> None:
    policy = _policy()
    summary = _source("summary", "CFO-01", 45, "action:salary")
    path = _source("account_path", "", 0)

    result = policy.combine_source_assessments(summary, path)

    assert result.score == 45
    assert result.independent_source_count == 1


@pytest.mark.parametrize("score", [70, 90])
def test_scores_70_and_90_require_two_independent_sources(score: int) -> None:
    policy = _policy()
    pairs = {
        70: (25, 45),
        90: (45, 45),
    }
    summary_quality, path_quality = pairs[score]
    summary = _source("summary", "CFO-01", summary_quality, "action:salary")
    path = _source("account_path", "CFO-01", path_quality, "account:employee_benefit")

    result = policy.combine_source_assessments(summary, path)

    assert result.score == score
    assert result.independent_source_count == 2


def test_repeated_semantic_fact_is_not_counted_as_a_second_source() -> None:
    policy = _policy()
    summary = _source("summary", "CFO-01", 45, "business:salary")
    path = _source("account_path", "CFO-01", 45, "business:salary")

    result = policy.combine_source_assessments(summary, path)

    assert result.score == 45
    assert result.independent_source_count == 1
    assert result.sources_independent is False


def test_same_fact_with_different_source_prefixes_is_not_independent() -> None:
    policy = _policy()
    summary = _source(
        "summary", "CFI-06", 45, "summary_fact:equipment_purchase"
    )
    path = _source(
        "account_path", "CFI-06", 45, "account_fact:equipment_purchase"
    )

    result = policy.combine_source_assessments(summary, path)

    assert result.score == 45
    assert result.sources_independent is False


def test_summary_adds_path_external_fact_even_when_path_adds_no_second_unique_fact() -> None:
    """防止把“摘要新增独立业务事实”误写成“两边都必须各有独有事实”。"""
    policy = _policy()
    summary = _source(
        "summary",
        "CFI-06",
        25,
        "business:equipment_purchase",
        "purpose:elevator_installation",
    )
    path = _source(
        "account_path",
        "CFI-06",
        45,
        "business:equipment_purchase",
    )

    result = policy.combine_source_assessments(summary, path)

    assert result.sources_independent is True
    assert result.independent_source_count == 2
    assert result.score == 70


def test_path_adds_summary_external_fact_even_when_summary_adds_no_second_unique_fact() -> None:
    """独立性判断对两个来源对称，路径新增分类事实也应形成第二来源。"""
    policy = _policy()
    summary = _source(
        "summary",
        "CFI-06",
        45,
        "business:equipment_purchase",
    )
    path = _source(
        "account_path",
        "CFI-06",
        25,
        "business:equipment_purchase",
        "purpose:elevator_installation",
    )

    result = policy.combine_source_assessments(summary, path)

    assert result.sources_independent is True
    assert result.independent_source_count == 2
    assert result.score == 70


@pytest.mark.parametrize("score", (70, 90))
def test_high_score_requires_two_independent_sources(score: int) -> None:
    policy = _policy()

    with pytest.raises(ValueError, match="两个独立来源"):
        policy.EvidenceAssessment("CFO-01", score, 1, False, False)


def test_generic_cash_direction_does_not_create_an_independent_source() -> None:
    policy = _policy()
    summary = _source("summary", "CFO-01", 10, "cash_direction:outflow")
    path = _source("account_path", "CFO-01", 45, "account:employee_benefit")

    result = policy.combine_source_assessments(summary, path)

    assert result.score == 45
    assert result.independent_source_count == 1
    assert result.sources_independent is False


def test_conflicting_sources_do_not_produce_a_usable_score() -> None:
    policy = _policy()
    summary = _source("summary", "CFO-01", 45, "action:salary")
    path = _source("account_path", "CFI-01", 45, "account:fixed_asset")

    result = policy.combine_source_assessments(summary, path)

    assert result.score is None
    assert result.conflict is True
    assert result.conflict_item_ids == ("CFI-01", "CFO-01")


def test_weak_multi_candidate_source_can_be_narrowed_by_strong_path() -> None:
    policy = _policy()
    summary = policy.EvidenceSourceAssessment(
        source=policy.EvidenceSource.SUMMARY,
        candidate_item_id="",
        candidate_item_ids=("CFO-04", "CFO-07", "CFI-06"),
        quality=policy.EvidenceQuality.WEAK,
        basis_text="摘要只有付款动作，不能区分采购、费用或购建资产",
        classification_facts=("action:payment",),
    )
    path = _source(
        "account_path",
        "CFO-04",
        45,
        "account:trade_payable_goods",
    )

    result = policy.combine_source_assessments(summary, path)

    assert result.candidate_item_id == "CFO-04"
    assert result.candidate_item_ids == ("CFO-04",)
    assert result.score == 55
    assert result.conflict is False


def test_disjoint_multi_candidate_sources_are_a_real_conflict() -> None:
    policy = _policy()
    summary = policy.EvidenceSourceAssessment(
        source=policy.EvidenceSource.SUMMARY,
        candidate_item_id="",
        candidate_item_ids=("CFO-04", "CFO-07"),
        quality=policy.EvidenceQuality.WEAK,
        basis_text="摘要支持经营支出候选",
        classification_facts=("action:operating_payment",),
    )
    path = _source(
        "account_path",
        "CFI-06",
        45,
        "account:fixed_asset_payable",
    )

    result = policy.combine_source_assessments(summary, path)

    assert result.score is None
    assert result.conflict is True
    assert result.conflict_item_ids == ("CFI-06", "CFO-04", "CFO-07")


def test_invalid_source_cannot_claim_a_candidate_or_facts() -> None:
    policy = _policy()

    with pytest.raises(ValueError, match="无效来源"):
        policy.EvidenceSourceAssessment(
            source=policy.EvidenceSource.SUMMARY,
            candidate_item_id="CFO-01",
            quality=policy.EvidenceQuality.INVALID,
            basis_text="构造测试原文",
            classification_facts=("action:salary",),
        )


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (99, "M0"),
        (100, "M1"),
        (999, "M1"),
        (1000, "M2"),
        (9999, "M2"),
        (10000, "M3"),
    ],
)
def test_reaching_a_materiality_threshold_moves_to_the_higher_level(
    amount: int,
    expected: str,
) -> None:
    policy = _policy()
    thresholds = MaterialityAmounts(
        overall_cent=10000,
        performance_cent=1000,
        trivial_cent=100,
    )

    assert policy.materiality_level(amount, thresholds).value == expected


def test_obsolete_effective_materiality_entry_is_removed() -> None:
    policy = _policy()

    assert not hasattr(policy, "effective_materiality")


@pytest.mark.parametrize(
    ("score", "actions", "post_review_minimums"),
    [
        *[
            (
                score,
                ("automatic_keep", "automatic_keep", "ai_review", "human_decision"),
                (None, None, 70, None),
            )
            for score in (0, 10, 20, 25, 35, 45, 50)
        ],
        *[
            (
                score,
                ("automatic_keep", "automatic_keep", "automatic_keep", "human_decision"),
                (None, None, None, None),
            )
            for score in (55, 70, 90)
        ],
    ],
)
def test_original_item_agreement_uses_the_first_approved_action_table(
    score: int,
    actions: tuple[str, ...],
    post_review_minimums: tuple[int | None, ...],
) -> None:
    policy = _policy()

    routes = tuple(
        policy.route_normal_decision(
            score,
            policy.OriginalItemState.AGREES,
            policy.MaterialityLevel(level),
        )
        for level in ("M0", "M1", "M2", "M3")
    )

    assert tuple(route.action.value for route in routes) == actions
    assert tuple(route.required_post_review_score for route in routes) == post_review_minimums


@pytest.mark.parametrize(
    ("score", "actions", "post_review_minimums"),
    [
        *[
            (
                score,
                ("ai_review", "double_ai_review", "double_ai_review", "human_decision"),
                (0, 0, 0, 0),
            )
            for score in (0, 10, 20, 25, 35, 45, 50)
        ],
        (55, ("ai_review", "ai_review", "double_ai_review", "human_decision"), (55, 55, 55, 55)),
        (70, ("automatic_fill", "ai_review", "ai_review", "human_decision"), (None, 70, 70, None)),
        (90, ("automatic_fill", "ai_review", "ai_review", "ai_review"), (None, 90, 90, 90)),
    ],
)
@pytest.mark.parametrize(
    "state",
    ["blank", "unstandardizable"],
)
def test_blank_or_unstandardizable_original_item_uses_the_second_action_table(
    score: int,
    actions: tuple[str, ...],
    post_review_minimums: tuple[int | None, ...],
    state: str,
) -> None:
    policy = _policy()

    routes = tuple(
        policy.route_normal_decision(
            score,
            policy.OriginalItemState(state),
            policy.MaterialityLevel(level),
        )
        for level in ("M0", "M1", "M2", "M3")
    )

    assert tuple(route.action.value for route in routes) == actions
    assert tuple(route.required_post_review_score for route in routes) == post_review_minimums


def test_conflicting_original_item_uses_change_instead_of_fill() -> None:
    policy = _policy()

    route = policy.route_normal_decision(
        90,
        policy.OriginalItemState.CONFLICTS,
        policy.MaterialityLevel.M0,
    )

    assert route.action.value == "automatic_change"


@pytest.mark.parametrize(
    ("score", "level"),
    [(score, level) for score in (0, 10, 20, 25, 35, 45, 50, 55) for level in ("M0", "M1")],
)
def test_valid_original_is_kept_when_change_burden_is_not_met(
    score: int,
    level: str,
) -> None:
    policy = _policy()

    route = policy.route_normal_decision(
        score,
        policy.OriginalItemState.CONFLICTS,
        policy.MaterialityLevel(level),
    )

    assert route.action.value == "automatic_keep"
    assert route.allowed_operations == frozenset()


@pytest.mark.parametrize("level", ["M0", "M1"])
def test_unresolved_candidate_keeps_a_standardized_original(level: str) -> None:
    policy = _policy()

    route = policy.route_decision(
        score=None,
        original_state=policy.OriginalItemState.PENDING_COMPARISON,
        materiality=policy.MaterialityLevel(level),
        source_conflict=True,
    )

    assert route.action.value == "automatic_keep"


@pytest.mark.parametrize("score", [0, 10, 20, 25, 35, 45, 50])
def test_valid_original_at_performance_materiality_gets_one_ai_retention_check(
    score: int,
) -> None:
    policy = _policy()

    route = policy.route_normal_decision(
        score,
        policy.OriginalItemState.AGREES,
        policy.MaterialityLevel.M2,
    )

    assert route.action.value == "ai_review"


def test_valid_original_score_55_conflict_at_performance_materiality_requires_ai() -> None:
    policy = _policy()

    route = policy.route_normal_decision(
        55,
        policy.OriginalItemState.CONFLICTS,
        policy.MaterialityLevel.M2,
    )

    assert route.action.value == "ai_review"
    assert route.required_post_review_score == 70


def test_source_conflict_at_performance_materiality_starts_ai_for_valid_original() -> None:
    policy = _policy()

    route = policy.route_decision(
        score=None,
        original_state=policy.OriginalItemState.PENDING_COMPARISON,
        materiality=policy.MaterialityLevel.M2,
        source_conflict=True,
    )

    assert route.action.value == "ai_review"


def test_source_conflict_without_valid_original_uses_two_ai_above_triviality() -> None:
    policy = _policy()

    route = policy.route_decision(
        score=None,
        original_state=policy.OriginalItemState.BLANK,
        materiality=policy.MaterialityLevel.M1,
        source_conflict=True,
    )

    assert route.action.value == "double_ai_review"


def test_unapproved_score_is_rejected_instead_of_being_rounded() -> None:
    policy = _policy()

    with pytest.raises(ValueError, match="不允许的证据分数"):
        policy.route_normal_decision(
            40,
            policy.OriginalItemState.AGREES,
            policy.MaterialityLevel.M1,
        )


def test_forced_checks_run_before_the_normal_action_table() -> None:
    policy = _policy()
    cases = (
        ({"invalid_input": True}, "isolate_invalid_input", "invalid_input"),
        ({"cash_scope_confirmed": False}, "confirm_cash_scope", "cash_scope"),
        ({"source_conflict": True}, "automatic_keep", "source_conflict"),
        ({"business_conflict": True}, "automatic_keep", "business_conflict"),
        ({"company_rule_conflict": True}, "human_decision", "company_rule_conflict"),
        ({"vat_base_missing": True}, "automatic_keep", "vat_base_missing"),
        ({"net_item_facts_missing": True}, "automatic_keep", "net_item_facts_missing"),
    )
    for changes, expected_action, expected_check in cases:
        arguments = {
            "score": 90,
            "original_state": policy.OriginalItemState.AGREES,
            "materiality": policy.MaterialityLevel.M1,
        }
        arguments.update(changes)
        route = policy.route_decision(**arguments)
        assert route.action.value == expected_action
        assert route.forced_check == expected_check


@pytest.mark.parametrize(
    ("level", "expected_action"),
    [
        ("M0", "automatic_keep"),
        ("M1", "automatic_keep"),
        ("M2", "ai_review"),
        ("M3", "human_decision"),
    ],
)
def test_source_conflict_uses_the_confirmed_materiality_route(
    level: str,
    expected_action: str,
) -> None:
    policy = _policy()

    route = policy.route_decision(
        score=None,
        original_state=policy.OriginalItemState.CONFLICTS,
        materiality=policy.MaterialityLevel(level),
        source_conflict=True,
    )

    assert route.action.value == expected_action
    assert route.allowed_operations == frozenset()


@pytest.mark.parametrize(
    ("level", "expected_action"),
    [
        ("M0", "automatic_keep"),
        ("M1", "automatic_keep"),
        ("M2", "automatic_keep"),
        ("M3", "human_decision"),
    ],
)
def test_candidate_business_conflict_does_not_displace_a_valid_original(
    level: str,
    expected_action: str,
) -> None:
    policy = _policy()

    route = policy.route_decision(
        score=90,
        original_state=policy.OriginalItemState.AGREES,
        materiality=policy.MaterialityLevel(level),
        business_conflict=True,
    )

    assert route.action.value == expected_action
    assert route.allowed_operations == frozenset()


@pytest.mark.parametrize(
    ("state", "level", "expected_action"),
    [
        ("agrees", "M0", "automatic_keep"),
        ("agrees", "M1", "automatic_keep"),
        ("agrees", "M2", "double_ai_review"),
        ("agrees", "M3", "human_decision"),
        ("blank", "M0", "low_amount_human_batch"),
        ("blank", "M1", "human_batch"),
        ("blank", "M2", "double_ai_review"),
        ("blank", "M3", "human_decision"),
    ],
)
def test_unknown_individual_tax_service_uses_its_own_forced_route(
    state: str,
    level: str,
    expected_action: str,
) -> None:
    policy = _policy()

    route = policy.route_decision(
        score=45,
        original_state=policy.OriginalItemState(state),
        materiality=policy.MaterialityLevel(level),
        individual_tax_fact_missing=True,
    )

    assert route.action.value == expected_action
    assert route.forced_check == "individual_tax_service"


def test_removed_direction_status_value_is_rejected() -> None:
    policy = _policy()

    with pytest.raises(ValueError, match="方向状态"):
        policy.route_decision(
            score=90,
            original_state=policy.OriginalItemState.AGREES,
            materiality=policy.MaterialityLevel.M0,
            direction_status="已批准反向模式",
        )


@pytest.mark.parametrize(
    ("level", "expected_action"),
    [
        ("M0", "automatic_fill"),
        ("M1", "ai_review"),
        ("M2", "ai_review"),
        ("M3", "human_decision"),
    ],
)
def test_direction_incompatibility_uses_the_normal_action_table(
    level: str, expected_action: str
) -> None:
    policy = _policy()

    route = policy.route_decision(
        score=70,
        original_state=policy.OriginalItemState.BLANK,
        materiality=policy.MaterialityLevel(level),
        direction_status="incompatible",
    )

    assert route.action.value == expected_action
    assert route.forced_check == ""


def test_removed_refund_route_argument_is_not_part_of_the_policy_api() -> None:
    policy = _policy()

    with pytest.raises(TypeError):
        policy.route_decision(
            score=70,
            original_state=policy.OriginalItemState.BLANK,
            materiality=policy.MaterialityLevel.M1,
            已移除退款专用路由=True,
        )
