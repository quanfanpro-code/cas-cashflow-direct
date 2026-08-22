from __future__ import annotations

import importlib

import pytest

from cashflow_direct.models import MaterialityAmounts


def _materiality():
    return importlib.import_module("cashflow_direct.materiality")


def _record(
    record_id: str,
    amount_cent: int,
    *,
    direction: str = "outflow",
    candidate: str = "CFO-04",
    level1: str = "应付账款",
    business_object: str = "采购商品",
    purpose: str = "日常采购",
    grouping_reliable: bool = True,
    grouping_reason: str = "候选、一级科目和明细用途均明确",
):
    module = _materiality()
    return module.MaterialityRecord(
        record_id=record_id,
        amount_cent=amount_cent,
        cash_direction=direction,
        candidate_item_id=candidate,
        standard_level1_account=level1,
        business_object=business_object,
        purpose=purpose,
        grouping_reliable=grouping_reliable,
        grouping_reason=grouping_reason,
    )


def _thresholds() -> MaterialityAmounts:
    return MaterialityAmounts(
        overall_cent=10_000,
        performance_cent=1_000,
        trivial_cent=100,
    )


def test_reliable_same_class_cumulative_amount_never_promotes_each_item() -> None:
    module = _materiality()
    records = (_record("A", -60), _record("B", -60))

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.single_level.value for item in results} == {"M0"}
    assert {item.same_class_total_cent for item in results} == {120}
    assert {item.effective_level.value for item in results} == {"M0"}
    assert {item.cumulative_level.value for item in results} == {"M1"}


def test_inflows_and_outflows_are_never_netted_or_grouped_together() -> None:
    module = _materiality()
    records = (
        _record("IN", 700, direction="inflow"),
        _record("OUT", -700, direction="outflow"),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.same_class_total_cent for item in results} == {700}
    assert {item.effective_level.value for item in results} == {"M1"}


def test_pending_candidate_uses_one_conservative_pending_group() -> None:
    module = _materiality()
    records = (
        _record("A", -600, candidate="", purpose="可能用途一"),
        _record("B", -600, candidate="", purpose="可能用途二"),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.same_class_total_cent for item in results} == {1_200}
    assert {item.cumulative_level.value for item in results} == {"M2"}
    assert {item.effective_level.value for item in results} == {"M1"}
    assert {item.grouping_status for item in results} == {"potential"}
    assert all(item.group_key[1] == "待判断" for item in results)


def test_potential_group_only_warns_and_never_promotes_materiality() -> None:
    module = _materiality()
    records = tuple(
        _record(
            component_id,
            -4_000,
            purpose="",
            grouping_reliable=False,
            grouping_reason="用途缺失",
        )
        for component_id in ("A", "B", "C")
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.single_level.value for item in results} == {"M2"}
    assert {item.cumulative_level.value for item in results} == {"M3"}
    assert {item.effective_level.value for item in results} == {"M2"}
    assert {item.grouping_status for item in results} == {"potential"}
    assert {item.grouping_reason for item in results} == {"用途缺失"}


def test_different_candidates_are_not_combined() -> None:
    module = _materiality()
    records = (
        _record("A", -600, candidate="CFO-04"),
        _record("B", -600, candidate="CFI-06"),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.same_class_total_cent for item in results} == {600}


def test_semantic_wording_does_not_split_same_candidate_account_and_purpose() -> None:
    module = _materiality()
    records = (
        _record(
            "A",
            -600,
            business_object="购买商品形成的结算",
            purpose="应付商品款",
        ),
        _record(
            "B",
            -600,
            business_object="商品或外部劳务支出",
            purpose="应付商品款",
        ),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.same_class_total_cent for item in results} == {1_200}
    assert len({item.group_id for item in results}) == 1


def test_duplicate_record_id_is_rejected_instead_of_double_counted() -> None:
    module = _materiality()

    with pytest.raises(ValueError, match="重复"):
        module.assess_materiality_records(
            (_record("A", -600), _record("A", -600)),
            _thresholds(),
        )


def test_same_class_records_share_one_stable_materiality_group_id() -> None:
    module = _materiality()

    results = module.assess_materiality_records(
        (_record("A", -6_000), _record("B", -6_000)),
        _thresholds(),
    )

    assert len({item.group_id for item in results}) == 1
    assert results[0].group_id.startswith("MATGRP_")
