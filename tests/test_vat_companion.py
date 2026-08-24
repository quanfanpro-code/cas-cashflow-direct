from __future__ import annotations

from dataclasses import replace

from cashflow_direct.components import ComponentSourceAllocation
from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.vat_companion import (
    apply_vat_companion_relations,
    build_vat_companion_relations,
)


def _component(
    component_id: str,
    account: str,
    *,
    voucher_key: str = "V-1",
    cash_delta_cent: int = -100,
) -> CashflowComponent:
    return CashflowComponent(
        component_id=component_id,
        voucher_key=voucher_key,
        summary="支付采购款",
        cash_delta_cent=cash_delta_cent,
        counterpart_accounts=(account,),
    )


def _decision(
    component_id: str,
    item_id: str,
    *,
    resolved: bool,
) -> ClassificationDecision:
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=item_id,
        system_item_name="购买商品、接受劳务支付的现金" if item_id else "",
        normal_direction="outflow",
        matched_rule_id="TEST",
        reason="构造测试",
        evidence_level="strong",
        resolved=resolved,
        decision_action="automatic_keep" if resolved else "human_decision",
    )


def test_split_vat_and_base_share_one_cash_source() -> None:
    components = (
        _component("CMP-BASE", "应付账款_供应商"),
        _component("CMP-VAT", "应交税费_应交增值税_进项税额"),
    )
    allocations = (
        ComponentSourceAllocation("CMP-BASE", "CASH-1", -100),
        ComponentSourceAllocation("CMP-VAT", "CASH-1", -13),
    )

    relation = build_vat_companion_relations(components, allocations)[0]

    assert relation.status == "unique"
    assert relation.vat_component_id == "CMP-VAT"
    assert relation.base_component_id == "CMP-BASE"
    assert relation.shared_entry_ids == ("CASH-1",)


def test_same_voucher_without_shared_cash_source_does_not_follow() -> None:
    components = (
        _component("CMP-BASE", "应付账款_供应商"),
        _component("CMP-VAT", "应交税费_应交增值税_进项税额"),
    )
    allocations = (
        ComponentSourceAllocation("CMP-BASE", "CASH-1", -100),
        ComponentSourceAllocation("CMP-VAT", "CASH-2", -13),
    )

    relation = build_vat_companion_relations(components, allocations)[0]

    assert relation.status == "missing"
    assert relation.base_component_id == ""


def test_two_possible_bases_are_reported_as_conflict() -> None:
    components = (
        _component("CMP-BASE-1", "应付账款_供应商甲"),
        _component("CMP-BASE-2", "其他应付款_供应商乙"),
        _component("CMP-VAT", "应交税费_应交增值税_进项税额"),
    )
    allocations = tuple(
        ComponentSourceAllocation(component.component_id, "CASH-1", -10)
        for component in components
    )

    relation = build_vat_companion_relations(components, allocations)[0]

    assert relation.status == "conflict"
    assert relation.base_component_id == ""


def test_standalone_tax_payment_is_not_a_vat_companion() -> None:
    components = (_component("CMP-TAX", "应交税费_未交增值税"),)
    allocations = (ComponentSourceAllocation("CMP-TAX", "CASH-1", -100),)

    assert build_vat_companion_relations(components, allocations) == ()


def test_reliable_base_decision_is_applied_to_vat() -> None:
    relations = build_vat_companion_relations(
        (
            _component("CMP-BASE", "应付账款_供应商"),
            _component("CMP-VAT", "应交税费_应交增值税_进项税额"),
        ),
        (
            ComponentSourceAllocation("CMP-BASE", "CASH-1", -100),
            ComponentSourceAllocation("CMP-VAT", "CASH-1", -13),
        ),
    )

    refreshed = apply_vat_companion_relations(
        (
            _decision("CMP-BASE", "CFO-04", resolved=True),
            _decision("CMP-VAT", "", resolved=False),
        ),
        relations,
    )
    vat = refreshed[1]

    assert vat.system_item_id == "CFO-04"
    assert vat.resolved is True
    assert vat.vat_base_missing is False
    assert vat.decision_action == "vat_follow_base"
    assert vat.vat_base_component_id == "CMP-BASE"


def test_pending_base_suppresses_a_separate_vat_decision() -> None:
    relations = build_vat_companion_relations(
        (
            _component("CMP-BASE", "应付账款_供应商"),
            _component("CMP-VAT", "应交税费_应交增值税_进项税额"),
        ),
        (
            ComponentSourceAllocation("CMP-BASE", "CASH-1", -100),
            ComponentSourceAllocation("CMP-VAT", "CASH-1", -13),
        ),
    )

    refreshed = apply_vat_companion_relations(
        (
            _decision("CMP-BASE", "CFO-04", resolved=False),
            _decision("CMP-VAT", "CFO-06", resolved=False),
        ),
        relations,
    )
    vat = refreshed[1]

    assert vat.system_item_id == "CFO-06"
    assert vat.resolved is False
    assert vat.decision_action == "vat_follow_base"
    assert vat.vat_base_missing is False
    assert vat.vat_base_component_id == "CMP-BASE"


def test_explicitly_excluded_base_excludes_vat_without_a_second_decision() -> None:
    components = (
        _component("CMP-BASE", "应付账款_供应商"),
        _component("CMP-VAT", "应交税费_应交增值税_进项税额"),
    )
    relations = build_vat_companion_relations(
        components,
        (
            ComponentSourceAllocation("CMP-BASE", "CASH-1", -100),
            ComponentSourceAllocation("CMP-VAT", "CASH-1", -13),
        ),
    )
    excluded_base = replace(
        _decision("CMP-BASE", "", resolved=True),
        excluded=True,
        decision_action="manual_exclude",
    )

    vat = apply_vat_companion_relations(
        (excluded_base, _decision("CMP-VAT", "CFO-06", resolved=False)),
        relations,
    )[1]

    assert vat.resolved is True
    assert vat.excluded is True
    assert vat.system_item_id == ""
    assert vat.decision_action == "vat_follow_base"
