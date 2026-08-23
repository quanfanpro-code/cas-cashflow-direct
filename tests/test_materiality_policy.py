from __future__ import annotations

import importlib
import unittest

import pytest

from cashflow_direct.models import MaterialityAmounts


class SingleItemMaterialityTests(unittest.TestCase):
    def test_assessment_contains_only_single_item_materiality(self) -> None:
        module = _materiality()
        records = (
            module.MaterialityRecord("A", -60),
            module.MaterialityRecord("B", -60),
        )

        results = module.assess_materiality_records(records, _thresholds())

        self.assertEqual(["M0", "M0"], [item.single_level.value for item in results])
        for item in results:
            self.assertFalse(hasattr(item, "same_class_total_cent"))
            self.assertFalse(hasattr(item, "cumulative_level"))
            self.assertFalse(hasattr(item, "group_id"))
            self.assertFalse(hasattr(item, "grouping_status"))


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
    return module.MaterialityRecord(record_id=record_id, amount_cent=amount_cent)


def _thresholds() -> MaterialityAmounts:
    return MaterialityAmounts(
        overall_cent=10_000,
        performance_cent=1_000,
        trivial_cent=100,
    )


def test_two_small_items_remain_individually_small() -> None:
    module = _materiality()
    records = (_record("A", -60), _record("B", -60))

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.single_level.value for item in results} == {"M0"}
    assert all(not hasattr(item, "group_id") for item in results)


def test_inflows_and_outflows_use_their_own_absolute_amount() -> None:
    module = _materiality()
    records = (
        _record("IN", 700, direction="inflow"),
        _record("OUT", -700, direction="outflow"),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.single_level.value for item in results} == {"M1"}


def test_candidate_state_cannot_change_single_item_materiality() -> None:
    module = _materiality()
    records = (
        _record("A", -600, candidate="", purpose="可能用途一"),
        _record("B", -600, candidate="", purpose="可能用途二"),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.single_level.value for item in results} == {"M1"}


def test_three_performance_level_items_do_not_become_overall_material() -> None:
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
    assert {item.single_level.value for item in results} == {"M2"}


def test_candidate_difference_does_not_affect_single_amount_level() -> None:
    module = _materiality()
    records = (
        _record("A", -600, candidate="CFO-04"),
        _record("B", -600, candidate="CFI-06"),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.single_level.value for item in results} == {"M1"}


def test_same_business_text_does_not_create_a_group() -> None:
    module = _materiality()
    records = (
        _record(
            "A",
            -600,
            business_object="商品采购",
            purpose="应付商品款",
        ),
        _record(
            "B",
            -600,
            business_object="商品采购",
            purpose="应付商品款",
        ),
    )

    results = module.assess_materiality_records(records, _thresholds())

    assert {item.single_level.value for item in results} == {"M1"}
    assert all(not hasattr(item, "group_id") for item in results)


def test_duplicate_record_id_is_rejected_instead_of_double_counted() -> None:
    module = _materiality()

    with pytest.raises(ValueError, match="重复"):
        module.assess_materiality_records(
            (_record("A", -600), _record("A", -600)),
            _thresholds(),
        )


def test_same_class_records_never_receive_a_group_id() -> None:
    module = _materiality()

    results = module.assess_materiality_records(
        (_record("A", -6_000), _record("B", -6_000)),
        _thresholds(),
    )

    assert {item.single_level.value for item in results} == {"M2"}
    assert all(not hasattr(item, "group_id") for item in results)
