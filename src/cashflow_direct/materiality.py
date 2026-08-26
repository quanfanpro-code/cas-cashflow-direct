from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cashflow_direct.decision_policy import (
    MaterialityLevel,
    materiality_level,
)
from cashflow_direct.models import ReviewBatch, UnresolvedDecision
from cashflow_direct.money import stable_id


@dataclass(frozen=True, slots=True)
class MaterialityRecord:
    record_id: str
    amount_cent: int


@dataclass(frozen=True, slots=True)
class MaterialityAssessment:
    record_id: str
    single_amount_cent: int
    single_level: MaterialityLevel


def assess_materiality_records(
    records: Sequence[MaterialityRecord],
    thresholds,
) -> tuple[MaterialityAssessment, ...]:
    seen: set[str] = set()
    for record in records:
        if record.record_id in seen:
            raise ValueError(f"记录编号重复：{record.record_id}")
        seen.add(record.record_id)
    return tuple(
        MaterialityAssessment(
            record_id=record.record_id,
            single_amount_cent=abs(record.amount_cent),
            single_level=materiality_level(record.amount_cent, thresholds),
        )
        for record in records
    )


def build_review_batches(
    unresolved: Sequence[UnresolvedDecision],
    performance_cent: int,
    all_leaf_item_ids: Sequence[str] = (),
) -> tuple[ReviewBatch, ...]:
    """把统一动作表已经指定为人工处理的剩余事项逐项列出。

    all_leaf_item_ids：全部叶子标准项目编号；强制人工复核批次据此生成
    "可改选任一标准项目（除原判项目外）"的备选清单。
    """
    batches: list[ReviewBatch] = []
    # 达到财务报表整体重要性的事项强制单独成批，备选为除原判外的全部叶子标准项目。
    for item in unresolved:
        if not item.mandatory:
            continue
        batches.append(
            ReviewBatch(
                batch_id=stable_id("REV", item.component_id, "MANDATORY"),
                component_ids=(item.component_id,),
                proposed_item_code=item.system_item_id,
                alternative_item_codes=tuple(
                    item_id
                    for item_id in all_leaf_item_ids
                    if item_id != item.system_item_id
                ),
                worst_case_impact_cent=abs(item.cash_delta_cent),
                reason="达到财务报表整体重要性，强制人工复核（无论自动判断是否已经确定）",
                baseline_statement_amount_cent=item.system_statement_amount_cent,
                cash_delta_cent=item.cash_delta_cent,
                representative_summary=item.summary_pattern,
                counterpart_group=item.counterpart_group,
                source_locations=item.source_locations,
                mandatory=True,
                baseline_item_code=item.baseline_item_code,
                follows_component_id=item.follows_component_id,
            )
        )

    for item in unresolved:
        if item.mandatory:
            continue
        alternatives = tuple(sorted(item.alternative_item_ids))
        if not alternatives:
            raise ValueError(
                "重大待复核事项没有可供人工选择的备选现流项目："
                + item.component_id
            )
        batches.append(
            ReviewBatch(
                batch_id=stable_id("REV", item.component_id),
                component_ids=(item.component_id,),
                proposed_item_code=item.system_item_id,
                alternative_item_codes=alternatives,
                worst_case_impact_cent=max(
                    abs(item.cash_delta_cent), item.group_impact_cent
                ),
                reason="自动判断仍未取得唯一决定，按业务组成逐项人工决定",
                baseline_statement_amount_cent=item.system_statement_amount_cent,
                cash_delta_cent=item.cash_delta_cent,
                representative_summary=item.summary_pattern,
                counterpart_group=item.counterpart_group,
                source_locations=item.source_locations,
                baseline_item_code=item.baseline_item_code,
                follows_component_id=item.follows_component_id,
            )
        )
    return tuple(batches)


def partition_review_batches(
    unresolved: Sequence[UnresolvedDecision],
    performance_cent: int,
    all_leaf_item_ids: Sequence[str] = (),
) -> tuple[tuple[ReviewBatch, ...], tuple[ReviewBatch, ...]]:
    """把逐项重要复核与可整批处理的低金额事项物理分开。"""
    low_items = tuple(
        item
        for item in unresolved
        if item.decision_action == "low_amount_human_batch"
        and not item.mandatory
        and abs(item.cash_delta_cent) < performance_cent
    )
    low_ids = {item.component_id for item in low_items}
    important_items = tuple(
        item for item in unresolved if item.component_id not in low_ids
    )
    important = build_review_batches(
        important_items,
        performance_cent,
        all_leaf_item_ids,
    )

    grouped: dict[tuple[str, ...], list[UnresolvedDecision]] = {}
    for item in low_items:
        key = (
            item.decision_action,
            item.cash_direction,
            item.system_candidate_signature,
            item.account_path_signature,
            item.summary_business_signature,
            item.evidence_status,
            item.forced_check_reason,
        )
        grouped.setdefault(key, []).append(item)

    low_batches: list[ReviewBatch] = []
    for members in grouped.values():
        first = members[0]
        component_ids = tuple(item.component_id for item in members)
        low_batches.append(
            ReviewBatch(
                batch_id=stable_id("LOW", *component_ids),
                component_ids=component_ids,
                proposed_item_code=first.system_item_id,
                alternative_item_codes=tuple(sorted(first.alternative_item_ids)),
                worst_case_impact_cent=sum(abs(item.cash_delta_cent) for item in members),
                reason="七项批次条件完全相同，可在批次主行一次选择并应用到全部明细",
                baseline_statement_amount_cent=sum(
                    item.system_statement_amount_cent for item in members
                ),
                cash_delta_cent=sum(item.cash_delta_cent for item in members),
                representative_summary=first.summary_pattern,
                counterpart_group=first.counterpart_group,
                source_locations=tuple(
                    dict.fromkeys(
                        location
                        for item in members
                        for location in item.source_locations
                    )
                ),
                baseline_item_code=first.baseline_item_code,
            )
        )
    return important, tuple(low_batches)
