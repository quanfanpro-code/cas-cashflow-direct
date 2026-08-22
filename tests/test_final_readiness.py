from __future__ import annotations

from dataclasses import replace

import pytest

from cashflow_direct.components import ComponentSourceAllocation
from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.validation import (
    validate_classification,
    validate_final_readiness,
)


def _component(*, amount: int = 100, anomalies: tuple[str, ...] = ()) -> CashflowComponent:
    return CashflowComponent(
        component_id="CMP-1",
        voucher_key="V-1",
        summary="销售回款",
        cash_delta_cent=amount,
        counterpart_accounts=("主营业务收入",),
        source_keys=("ENT-1",),
        anomalies=anomalies,
    )


def _decision() -> ClassificationDecision:
    return ClassificationDecision(
        component_id="CMP-1",
        system_item_id="CFO-01",
        system_item_name="销售商品、提供劳务收到的现金",
        normal_direction="inflow",
        matched_rule_id="TEST",
        reason="两个来源一致",
        evidence_level="strong",
        resolved=True,
        evidence_score=90,
        decision_action="automatic_fill",
        materiality_level="M0",
    )


def test_classification_allows_explicit_pending_action_but_not_silent_blank() -> None:
    component = _component()
    pending = replace(
        _decision(),
        system_item_id="",
        system_item_name="",
        resolved=False,
        evidence_score=0,
        decision_action="low_amount_human_batch",
    )
    silent = replace(pending, decision_action="")

    assert validate_classification((component,), (pending,)).valid is True
    checked = validate_classification((component,), (silent,))
    assert checked.valid is False
    assert any("没有后续动作" in error for error in checked.errors)


@pytest.mark.parametrize(
    "action",
    ["ai_double_followup_review", "ai_third_review"],
)
def test_new_ai_followup_actions_are_pending_not_completed(action: str) -> None:
    component = _component()
    incorrect = replace(_decision(), decision_action=action, resolved=True)

    checked = validate_classification((component,), (incorrect,))

    assert checked.valid is False
    assert any("仍标成待处理动作" in error for error in checked.errors)


def test_final_readiness_requires_every_business_to_be_decided_or_excluded() -> None:
    component = _component()
    pending = replace(
        _decision(), resolved=False, decision_action="human_decision"
    )

    checked = validate_final_readiness(
        (component,),
        (pending,),
        (ComponentSourceAllocation("CMP-1", "ENT-1", 100),),
    )

    assert checked.valid is False
    assert any("仍待人工决定" in error for error in checked.errors)


def test_final_readiness_rejects_illegal_input_and_allocation_mismatch() -> None:
    component = _component(anomalies=("summary_empty",))

    checked = validate_final_readiness(
        (component,),
        (_decision(),),
        (ComponentSourceAllocation("CMP-1", "ENT-1", 90),),
    )

    assert checked.valid is False
    assert any("非法输入" in error for error in checked.errors)
    assert any("金额分配不守恒" in error for error in checked.errors)


def test_final_readiness_passes_without_an_extra_final_confirmation() -> None:
    checked = validate_final_readiness(
        (_component(),),
        (_decision(),),
        (ComponentSourceAllocation("CMP-1", "ENT-1", 100),),
        ai_tasks_missing=0,
        mapping_complete=True,
        versions_consistent=True,
    )

    assert checked.valid is True
    assert checked.errors == ()
