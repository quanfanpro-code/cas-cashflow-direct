from __future__ import annotations

from cashflow_direct.consistency import (
    apply_consistency_forced_checks,
    find_consistency_groups,
)
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
    account: str = "其他应付款_同一对象",
    source_file: str = "FILE-1",
) -> CashflowComponent:
    return CashflowComponent(
        component_id=component_id,
        voucher_key=voucher_key,
        summary=summary,
        cash_delta_cent=amount_cent,
        counterpart_accounts=(account,),
        source_keys=(f"ENT-{component_id}",),
        voucher_date="2026/6/15",
        voucher_no="70",
        source_file_ids=(source_file,),
    )


def decision(
    component_id: str,
    item_id: str,
    score: int = 70,
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
        normal_direction="outflow" if item_id in {"CFO-04", "CFI-06"} else "inflow",
        matched_rule_id="TEST",
        reason="两个原始来源形成候选",
        evidence_level="high" if score >= 70 else "medium",
        excluded=excluded,
        resolved=not excluded,
        evidence_score=score,
        decision_action="automatic_fill",
        materiality_level="M0",
    )


def test_same_summary_but_different_complete_paths_never_form_one_group() -> None:
    components = (
        component("C1", 6_000_000, account="预付账款_对象甲"),
        component("C2", -2_000_000, account="应付账款_对象乙"),
    )
    decisions = (decision("C1", "CFO-03"), decision("C2", "CFI-06"))

    assert find_consistency_groups(components, decisions, MATERIALITY) == ()


def test_true_same_source_with_different_items_forms_one_group() -> None:
    components = (component("C1", 71_760_000), component("C2", -21_760_000))
    decisions = (decision("C1", "CFO-03"), decision("C2", "CFI-06", 45))

    groups = find_consistency_groups(components, decisions, MATERIALITY)

    assert len(groups) == 1
    assert groups[0].component_ids == ("C1", "C2")
    assert groups[0].gross_cent == 93_520_000
    assert groups[0].net_cent == 50_000_000
    assert groups[0].materiality_level == "M3"


def test_same_source_group_uses_the_unified_materiality_boundaries() -> None:
    cases = (
        (499_999, "M0"),
        (500_000, "M1"),
        (4_999_999, "M1"),
        (5_000_000, "M2"),
        (9_999_999, "M2"),
        (10_000_000, "M3"),
    )
    for gross_cent, expected_level in cases:
        first = gross_cent // 2
        second = gross_cent - first
        group = find_consistency_groups(
            (component("C1", first), component("C2", -second)),
            (decision("C1", "CFO-03"), decision("C2", "CFI-06")),
            MATERIALITY,
        )[0]
        assert group.materiality_level == expected_level


def test_blank_summary_equal_items_and_excluded_items_do_not_create_groups() -> None:
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
        assert find_consistency_groups(components, decisions, MATERIALITY) == ()


def test_true_same_source_inconsistency_preserves_scores_and_uses_single_amount_route() -> None:
    components = (component("C1", 300_000), component("C2", -300_000))
    decisions = (decision("C1", "CFO-03", 70), decision("C2", "CFI-06", 45))

    outcome = apply_consistency_forced_checks(
        find_consistency_groups(components, decisions, MATERIALITY),
        decisions,
    )

    assert {item.evidence_score for item in outcome.decisions} == {70, 45}
    assert all(item.business_conflict for item in outcome.decisions)
    assert {item.decision_action for item in outcome.decisions} == {
        "low_amount_human_batch"
    }
    assert all(not item.resolved for item in outcome.decisions)


def test_m0_true_same_source_inconsistency_uses_low_amount_human_batch() -> None:
    components = (component("C1", 100_000), component("C2", -100_000))
    decisions = (decision("C1", "CFO-03"), decision("C2", "CFI-06", 45))

    outcome = apply_consistency_forced_checks(
        find_consistency_groups(components, decisions, MATERIALITY),
        decisions,
    )

    assert {item.decision_action for item in outcome.decisions} == {
        "low_amount_human_batch"
    }
