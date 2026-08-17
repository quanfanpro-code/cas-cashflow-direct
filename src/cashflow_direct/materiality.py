from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from cashflow_direct.models import ReviewBatch, UnresolvedDecision
from cashflow_direct.money import stable_id


def build_review_batches(
    unresolved: Sequence[UnresolvedDecision],
    performance_cent: int,
) -> tuple[ReviewBatch, ...]:
    """只把达到实际执行重要性的严格同质剩余风险送人工。"""
    grouped: dict[tuple[object, ...], list[UnresolvedDecision]] = defaultdict(list)
    for item in unresolved:
        key = (
            item.cash_direction,
            item.original_item,
            item.system_item_id,
            item.adjudication_status,
            item.counterpart_group,
            item.summary_pattern,
            tuple(sorted(item.alternative_item_ids)),
        )
        grouped[key].append(item)

    batches: list[ReviewBatch] = []
    for key, items in grouped.items():
        worst_case = max(
            sum(abs(item.cash_delta_cent) for item in items),
            max((item.group_impact_cent for item in items), default=0),
        )
        if worst_case < performance_cent:
            continue
        component_ids = tuple(item.component_id for item in items)
        alternatives = tuple(sorted(items[0].alternative_item_ids))
        if not alternatives:
            raise ValueError(
                "重大待复核事项没有可供人工选择的备选现流项目："
                + "、".join(component_ids)
            )
        batches.append(
            ReviewBatch(
                batch_id=stable_id("REV", *key, *component_ids),
                component_ids=component_ids,
                proposed_item_code=items[0].system_item_id,
                alternative_item_codes=alternatives,
                worst_case_impact_cent=worst_case,
                reason="自动判断仍未收口，且同质事项最不利重分类毛额达到实际执行的重要性",
                baseline_statement_amount_cent=sum(
                    item.system_statement_amount_cent or abs(item.cash_delta_cent)
                    for item in items
                ),
                cash_delta_cent=sum(item.cash_delta_cent for item in items),
                representative_summary=items[0].summary_pattern,
                counterpart_group=items[0].counterpart_group,
                source_locations=tuple(
                    dict.fromkeys(
                        location
                        for item in items
                        for location in item.source_locations
                    )
                ),
            )
        )
    return tuple(batches)
