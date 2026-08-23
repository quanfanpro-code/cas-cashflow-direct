from __future__ import annotations

from collections import defaultdict
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
    cash_direction: str
    candidate_item_id: str
    standard_level1_account: str
    business_object: str
    purpose: str
    grouping_reliable: bool = True
    grouping_reason: str = ""

    def __post_init__(self) -> None:
        if self.cash_direction not in {"inflow", "outflow"}:
            raise ValueError("现金方向只能是 inflow 或 outflow")


@dataclass(frozen=True, slots=True)
class MaterialityAssessment:
    record_id: str
    single_amount_cent: int
    same_class_total_cent: int
    single_level: MaterialityLevel
    cumulative_level: MaterialityLevel
    group_key: tuple[str, ...]
    group_id: str
    grouping_status: str
    grouping_reason: str


def _same_class_key(record: MaterialityRecord) -> tuple[str, ...]:
    if not record.candidate_item_id:
        return (
            record.cash_direction,
            "待判断",
            record.standard_level1_account,
            record.business_object,
        )
    return (
        record.cash_direction,
        record.candidate_item_id,
        record.standard_level1_account,
        record.business_object,
        record.purpose,
    )


def assess_materiality_records(
    records: Sequence[MaterialityRecord],
    thresholds,
) -> tuple[MaterialityAssessment, ...]:
    seen: set[str] = set()
    totals: dict[tuple[str, ...], int] = defaultdict(int)
    keys: dict[str, tuple[str, ...]] = {}
    for record in records:
        if record.record_id in seen:
            raise ValueError(f"记录编号重复，不能重复累计：{record.record_id}")
        seen.add(record.record_id)
        key = _same_class_key(record)
        keys[record.record_id] = key
        totals[key] += abs(record.amount_cent)

    results: list[MaterialityAssessment] = []
    for record in records:
        key = keys[record.record_id]
        single_level = materiality_level(record.amount_cent, thresholds)
        cumulative_level = materiality_level(totals[key], thresholds)
        reliable = bool(record.candidate_item_id and record.grouping_reliable)
        grouping_status = "reliable" if reliable else "potential"
        results.append(MaterialityAssessment(
            record_id=record.record_id,
            single_amount_cent=abs(record.amount_cent),
            same_class_total_cent=totals[key],
            single_level=single_level,
            cumulative_level=cumulative_level,
            group_key=key,
            group_id=stable_id("MATGRP", grouping_status, *key),
            grouping_status=grouping_status,
            grouping_reason=(
                record.grouping_reason
                or "候选、标准一级科目和明细用途均明确"
                if reliable
                else record.grouping_reason or "同类依据不足，仅作潜在累计风险提示"
            ),
        ))
    return tuple(results)


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
            )
        )
    return tuple(batches)
