from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cashflow_direct.decision_policy import (
    MaterialityLevel,
    materiality_level,
)
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    ReviewBatch,
    UnresolvedDecision,
)
from cashflow_direct.money import stable_id
from cashflow_direct.rule_registry import default_rule_registry


_DEPRECATED_ACTIONS = default_rule_registry().deprecated_actions


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
    """只保留重要或明确人工事项；低金额空白原项目必须先完成系统兜底。"""
    low_items = tuple(
        item
        for item in unresolved
        if item.decision_action in _DEPRECATED_ACTIONS
        and not item.mandatory
        and abs(item.cash_delta_cent) < performance_cent
    )
    if low_items:
        raise ValueError("低于实际执行重要性的事项必须先完成系统兜底，不能进入低金额人工批次")
    important_items = tuple(unresolved)
    important = build_review_batches(
        important_items,
        performance_cent,
        all_leaf_item_ids,
    )

    return important, ()


def build_low_amount_fallback_batches(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
) -> tuple[ReviewBatch, ...]:
    """把已经生效的低金额系统兜底决定逐业务送入可抽查明细表。"""
    component_by_id = {item.component_id: item for item in components}
    return tuple(
        ReviewBatch(
            batch_id=stable_id("FALLBACK", decision.component_id),
            component_ids=(decision.component_id,),
            proposed_item_code=decision.system_item_id,
            alternative_item_codes=(),
            worst_case_impact_cent=abs(component_by_id[decision.component_id].cash_delta_cent),
            reason=decision.reason,
            baseline_statement_amount_cent=abs(component_by_id[decision.component_id].cash_delta_cent),
            cash_delta_cent=component_by_id[decision.component_id].cash_delta_cent,
            representative_summary=component_by_id[decision.component_id].summary,
            counterpart_group="、".join(component_by_id[decision.component_id].counterpart_accounts),
            baseline_item_code=decision.system_item_id,
            fallback_source=decision.fallback_source,
            fallback_step=decision.fallback_step,
        )
        for decision in decisions
        if decision.decision_source == "system_low_amount_fallback"
        and decision.resolved
        and not decision.excluded
    )
