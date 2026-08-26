from __future__ import annotations

from dataclasses import replace

import pytest

from cashflow_direct.exclusion_policy import authorize_exclusion
from cashflow_direct.models import CashflowComponent, ClassificationDecision


def component(amount_cent: int = -10_000) -> CashflowComponent:
    return CashflowComponent(
        component_id="C1",
        voucher_key="V1",
        summary="真实银行付款",
        cash_delta_cent=amount_cent,
        source_keys=("E1",),
    )


def decision(**changes: object) -> ClassificationDecision:
    current = ClassificationDecision(
        component_id="C1",
        system_item_id="",
        system_item_name="",
        normal_direction="outflow",
        matched_rule_id="NO-BUSINESS-CANDIDATE",
        reason="尚未取得唯一分类",
        evidence_level="invalid",
        resolved=False,
        evidence_score=0,
        source_conflict=True,
        decision_action="human_decision",
    )
    return replace(current, **changes)


@pytest.mark.parametrize(
    "basis",
    ("无法分类", "无效输入", "低金额", "证据不足", "候选不唯一", "来源冲突"),
)
def test_free_text_uncertainty_never_authorizes_real_cash_exclusion(basis: str) -> None:
    with pytest.raises(ValueError, match="不是排除依据"):
        authorize_exclusion(
            component(),
            decision(),
            "",
            {"requested_exclusion": {"basis": basis}},
        )


def test_zero_amount_requires_matching_zero_fact() -> None:
    authorized = authorize_exclusion(
        component(0),
        decision(),
        "zero_amount",
        {"requested_exclusion": {}},
    )

    assert authorized.authorized is True
    assert authorized.exclusion_type == "zero_amount"

    with pytest.raises(ValueError, match="金额不为零"):
        authorize_exclusion(
            component(),
            decision(),
            "zero_amount",
            {"requested_exclusion": {}},
        )


def test_confirmed_adjustment_requires_structured_type_basis_operator_and_amount() -> None:
    request = {
        "adjustment_type": "审计确认的现金调整",
        "basis": "调整底稿A-1",
        "operator": "复核人",
        "adjustment_cent": -10_000,
    }

    authorized = authorize_exclusion(
        component(),
        decision(),
        "confirmed_adjustment",
        {"requested_exclusion": request},
    )

    assert authorized.authorized is True
    assert authorized.confirmed_adjustment_cent == -10_000

    for missing_key in request:
        incomplete = {key: value for key, value in request.items() if key != missing_key}
        with pytest.raises(ValueError, match="结构化"):
            authorize_exclusion(
                component(),
                decision(),
                "confirmed_adjustment",
                {"requested_exclusion": incomplete},
            )


def test_unknown_exclusion_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="排除类型无效"):
        authorize_exclusion(
            component(),
            decision(),
            "无法分类",
            {"requested_exclusion": {}},
        )
