from __future__ import annotations

import importlib
from dataclasses import replace

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
    overall_cent=10_000,
    performance_cent=5_000,
    trivial_cent=500,
)


def _component(component_id: str, account: str, amount: int) -> CashflowComponent:
    return CashflowComponent(
        component_id=component_id,
        voucher_key="V-1",
        summary="支付同一批款项",
        cash_delta_cent=-amount,
        counterpart_accounts=(account,),
        source_file_ids=("F-1",),
    )


def _decision(
    component_id: str,
    item_id: str,
    score: int,
    *,
    original_item_id: str = "",
) -> ClassificationDecision:
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=item_id,
        system_item_name=item_id,
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="两个原始来源形成候选",
        evidence_level="medium",
        resolved=True,
        evidence_score=score,
        decision_action="automatic_fill",
        materiality_level="M0",
        original_item_state="agrees" if original_item_id else "blank",
        original_standard_item_id=original_item_id,
    )


def test_same_summary_but_different_account_paths_are_not_forced_into_one_group() -> None:
    components = (
        _component("C-1", "短期借款", 300),
        _component("C-2", "应付职工薪酬", 300),
    )
    decisions = (
        _decision("C-1", "CFF-04", 70),
        _decision("C-2", "CFO-05", 70),
    )

    assert find_consistency_groups(components, decisions, MATERIALITY) == ()


def test_true_same_source_inconsistency_uses_each_items_own_amount_level() -> None:
    components = (
        _component("C-1", "其他应付款_同一对象", 300),
        _component("C-2", "其他应付款_同一对象", 300),
    )
    decisions = (
        _decision("C-1", "CFO-07", 70),
        _decision("C-2", "CFF-06", 45),
    )
    groups = find_consistency_groups(components, decisions, MATERIALITY)

    resolution = apply_consistency_forced_checks(groups, decisions)

    assert len(groups) == 1
    assert groups[0].materiality_level == "M1"
    assert {item.evidence_score for item in resolution.decisions} == {70, 45}
    assert all(item.business_conflict for item in resolution.decisions)
    assert {
        item.decision_action for item in resolution.decisions
    } == {"human_decision"}
    assert all(not item.resolved for item in resolution.decisions)


def test_m0_true_inconsistency_uses_human_decision_before_system_fallback() -> None:
    components = (
        _component("C-1", "其他应付款_同一对象", 100),
        _component("C-2", "其他应付款_同一对象", 100),
    )
    decisions = (
        _decision("C-1", "CFO-07", 70),
        _decision("C-2", "CFF-06", 45),
    )

    resolution = apply_consistency_forced_checks(
        find_consistency_groups(components, decisions, MATERIALITY),
        decisions,
    )

    assert {
        item.decision_action for item in resolution.decisions
    } == {"human_decision"}


def test_true_same_source_conflict_keeps_each_valid_original_below_overall() -> None:
    components = (
        _component("C-1", "其他应付款_同一对象", 300),
        _component("C-2", "其他应付款_同一对象", 300),
    )
    decisions = (
        _decision("C-1", "CFO-07", 70, original_item_id="CFO-03"),
        _decision("C-2", "CFF-06", 45, original_item_id="CFO-05"),
    )

    resolution = apply_consistency_forced_checks(
        find_consistency_groups(components, decisions, MATERIALITY), decisions
    )

    assert {item.system_item_id for item in resolution.decisions} == {
        "CFO-03",
        "CFO-05",
    }
    assert {item.decision_action for item in resolution.decisions} == {
        "automatic_keep"
    }
    assert all(item.resolved for item in resolution.decisions)
    assert resolution.unresolved == ()


def test_consistency_forced_check_uses_the_unified_decision_router(
    monkeypatch,
) -> None:
    consistency_module = importlib.import_module("cashflow_direct.consistency")
    policy_module = importlib.import_module("cashflow_direct.decision_policy")
    calls = []

    def traced_route(**arguments):
        calls.append(arguments)
        return policy_module.route_decision(**arguments)

    monkeypatch.setattr(
        consistency_module,
        "route_decision",
        traced_route,
        raising=False,
    )
    components = (
        _component("C-1", "其他应付款_同一对象", 100),
        _component("C-2", "其他应付款_同一对象", 100),
    )
    decisions = (
        _decision("C-1", "CFO-07", 70),
        _decision("C-2", "CFF-06", 45),
    )

    apply_consistency_forced_checks(
        find_consistency_groups(components, decisions, MATERIALITY), decisions
    )

    assert len(calls) == 2
    assert all(call["business_conflict"] is True for call in calls)
