from __future__ import annotations

from dataclasses import replace

import pytest

from cashflow_direct.materiality import partition_review_batches
from cashflow_direct.models import UnresolvedDecision


def unresolved(component_id: str = "C1", **changes: object) -> UnresolvedDecision:
    current = UnresolvedDecision(
        component_id=component_id,
        cash_delta_cent=-10_000,
        cash_direction="outflow",
        original_item="",
        system_item_id="CFO-07",
        review_status="低金额人工批量",
        counterpart_group="周转材料_低值易耗品",
        summary_pattern="采购植株",
        alternative_item_ids=("CFO-04", "CFO-07"),
        reason="复核后仍待处理",
        decision_action="low_amount_human_batch",
        system_candidate_signature="CFO-04|CFO-07",
        account_path_signature="周转材料_低值易耗品",
        summary_business_signature="采购植株",
        evidence_status="路径25|摘要25|冲突",
        forced_check_reason="同分不同项目",
    )
    return replace(current, **changes)


def test_partition_separates_important_and_low_amount_batches() -> None:
    low = unresolved("LOW")
    performance = unresolved(
        "PERF",
        cash_delta_cent=-100_000,
        decision_action="human_decision",
        review_status="达到实际执行重要性后仍未决",
    )
    explicit = unresolved(
        "EXPLICIT",
        decision_action="human_decision",
        review_status="统一行动表明确人工决定",
    )

    important, low_batches = partition_review_batches(
        (low, performance, explicit),
        performance_cent=100_000,
        all_leaf_item_ids=("CFO-04", "CFO-07"),
    )

    assert [batch.component_ids for batch in important] == [("PERF",), ("EXPLICIT",)]
    assert [batch.component_ids for batch in low_batches] == [("LOW",)]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("decision_action", "human_decision"),
        ("cash_direction", "inflow"),
        ("system_candidate_signature", "CFO-03"),
        ("account_path_signature", "管理费用_办公费"),
        ("summary_business_signature", "退押金"),
        ("evidence_status", "路径25|摘要10"),
        ("forced_check_reason", "业务组冲突"),
    ),
)
def test_any_of_seven_batch_keys_changed_prevents_merging(
    field: str,
    value: str,
) -> None:
    first = unresolved("C1")
    second = replace(unresolved("C2"), **{field: value})

    important, low_batches = partition_review_batches(
        (first, second),
        performance_cent=100_000,
        all_leaf_item_ids=("CFO-04", "CFO-07"),
    )

    if field == "decision_action":
        assert len(important) == 1
        assert len(low_batches) == 1
    else:
        assert not important
        assert len(low_batches) == 2


def test_same_seven_keys_merge_and_sum_cash_amount() -> None:
    first = unresolved("C1", cash_delta_cent=-10_000, source_locations=("A2",))
    second = unresolved("C2", cash_delta_cent=-20_000, source_locations=("A3",))

    important, low_batches = partition_review_batches(
        (first, second),
        performance_cent=100_000,
        all_leaf_item_ids=("CFO-04", "CFO-07"),
    )

    assert not important
    assert len(low_batches) == 1
    assert low_batches[0].component_ids == ("C1", "C2")
    assert low_batches[0].cash_delta_cent == -30_000
    assert low_batches[0].source_locations == ("A2", "A3")
