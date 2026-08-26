from __future__ import annotations

from dataclasses import replace

import pytest

from cashflow_direct.classification import load_rule_pack
from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.statement import aggregate_statement, reconcile_cash


RULES = load_rule_pack(__import__("pathlib").Path(__file__).resolve().parents[1])


def component(component_id: str, amount_cent: int, original_item: str = "") -> CashflowComponent:
    return CashflowComponent(
        component_id=component_id,
        voucher_key=component_id,
        summary="测试业务",
        cash_delta_cent=amount_cent,
        original_item_text=original_item,
    )


def decision(
    component_id: str,
    *,
    resolved: bool,
    item_id: str = "",
    original_item_id: str = "",
) -> ClassificationDecision:
    item = RULES.item_by_id.get(item_id)
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=item_id,
        system_item_name="" if item is None else item.name,
        normal_direction="net" if item is None else item.normal_direction,
        matched_rule_id="TEST",
        reason="测试",
        evidence_level="medium",
        resolved=resolved,
        evidence_score=45,
        original_item_state="blank" if not original_item_id else "agrees",
        original_standard_item_id=original_item_id,
        decision_action="automatic_fill" if resolved else "human_decision",
    )


@pytest.mark.parametrize(
    "case",
    ("all_classified", "all_pending", "partly_pending"),
)
def test_cash_bridge_never_drops_pending_cash(case: str) -> None:
    classified = component("C1", 60_000)
    pending = component("C2", 40_000)
    if case == "all_classified":
        decisions = (
            decision("C1", resolved=True, item_id="CFO-03"),
            decision("C2", resolved=True, item_id="CFO-03"),
        )
    elif case == "all_pending":
        decisions = (
            decision("C1", resolved=False),
            decision("C2", resolved=False),
        )
    else:
        decisions = (
            decision("C1", resolved=True, item_id="CFO-03"),
            decision("C2", resolved=False),
        )
    statement = aggregate_statement((classified, pending), decisions, RULES)

    result = reconcile_cash(
        statement,
        opening_cent=1_000_000,
        closing_cent=1_100_000,
        fx_cent=0,
        components=(classified, pending),
        decisions=decisions,
    )

    assert (
        result.classified_net_cent
        + result.pending_net_cent
        + result.fx_cent
        + result.confirmed_adjustment_cent
        + result.bridge_difference_cent
        == result.closing_cent - result.opening_cent
    )
    assert result.bridge_difference_cent == 0


def test_bridge_success_is_not_final_success_while_pending_cash_remains() -> None:
    pending = component("C1", 100_000)
    current = decision("C1", resolved=False)
    statement = aggregate_statement((pending,), (current,), RULES)

    result = reconcile_cash(
        statement,
        1_000_000,
        1_100_000,
        0,
        components=(pending,),
        decisions=(current,),
    )

    assert result.status == "现金变动桥接相符、现金流量表尚待分类"
    assert result.pending_component_ids == ("C1",)
    assert result.final_difference_cent == 100_000


def test_final_success_requires_pending_cash_to_be_zero() -> None:
    current_component = component("C1", 100_000)
    current_decision = decision("C1", resolved=True, item_id="CFO-03")
    statement = aggregate_statement((current_component,), (current_decision,), RULES)

    result = reconcile_cash(
        statement,
        1_000_000,
        1_100_000,
        0,
        components=(current_component,),
        decisions=(current_decision,),
    )

    assert result.status == "最终现金流量表勾稽成功"
    assert result.pending_net_cent == 0
    assert result.final_difference_cent == 0


def test_unresolved_valid_original_stays_in_baseline_but_blocks_final_success() -> None:
    current_component = component("C1", -100_000, "支付其他与经营活动有关的现金")
    current_decision = decision(
        "C1",
        resolved=False,
        item_id="CFO-04",
        original_item_id="CFO-07",
    )

    statement = aggregate_statement((current_component,), (current_decision,), RULES)
    result = reconcile_cash(
        statement,
        1_000_000,
        900_000,
        0,
        components=(current_component,),
        decisions=(current_decision,),
    )

    assert statement.values["CFO-07"] == 100_000
    assert result.classified_net_cent == -100_000
    assert result.pending_net_cent == 0
    assert result.status == "现金变动桥接相符、现金流量表尚待分类"
    assert result.pending_component_ids == ("C1",)


def test_offsetting_unresolved_cash_cannot_fake_final_success() -> None:
    inflow = component("C-IN", 100_000)
    outflow = component("C-OUT", -100_000)
    decisions = (
        decision("C-IN", resolved=False),
        decision("C-OUT", resolved=False),
    )
    statement = aggregate_statement((inflow, outflow), decisions, RULES)

    result = reconcile_cash(
        statement,
        1_000_000,
        1_000_000,
        0,
        components=(inflow, outflow),
        decisions=decisions,
    )

    assert result.pending_net_cent == 0
    assert result.bridge_difference_cent == 0
    assert result.final_difference_cent == 0
    assert result.pending_component_ids == ("C-IN", "C-OUT")
    assert result.status == "现金变动桥接相符、现金流量表尚待分类"


def test_confirmed_adjustment_remains_in_cash_bridge() -> None:
    current_component = component("C1", -10_000)
    current_decision = replace(
        decision("C1", resolved=True),
        excluded=True,
        exclusion_type="confirmed_adjustment",
        confirmed_adjustment_cent=-10_000,
    )
    statement = aggregate_statement((current_component,), (current_decision,), RULES)

    result = reconcile_cash(
        statement,
        1_000_000,
        990_000,
        0,
        components=(current_component,),
        decisions=(current_decision,),
        confirmed_adjustment_cent=-10_000,
    )

    assert result.confirmed_adjustment_cent == -10_000
    assert result.bridge_difference_cent == 0
