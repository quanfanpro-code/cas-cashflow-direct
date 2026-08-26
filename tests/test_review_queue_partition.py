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


def test_low_amount_human_batch_is_rejected_because_system_fallback_must_run_first() -> None:
    low = unresolved("LOW")

    with pytest.raises(ValueError, match="低于实际执行重要性的事项必须先完成系统兜底"):
        partition_review_batches(
            (low,),
            performance_cent=100_000,
            all_leaf_item_ids=("CFO-04", "CFO-07"),
        )


def test_partition_keeps_only_important_or_explicit_human_decisions() -> None:
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
        (performance, explicit),
        performance_cent=100_000,
        all_leaf_item_ids=("CFO-04", "CFO-07"),
    )

    assert [batch.component_ids for batch in important] == [("PERF",), ("EXPLICIT",)]
    assert low_batches == ()
