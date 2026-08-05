from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.money import stable_id, statement_amount_cent


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    group_id: str
    component_ids: tuple[str, ...]
    component_amounts_cent: tuple[int, ...]
    signature: tuple[str, ...]
    default_decision: str
    worst_case_impact_cent: int
    blocks_manual_completion: bool
    item_id: str = ""
    baseline_statement_amount_cent: int = 0


@dataclass(frozen=True, slots=True)
class DuplicateAdjustment:
    group_id: str
    component_id: str
    cash_delta_cent: int
    reason: str


def _normalize_summary(value: str) -> str:
    return re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】]+", "", value).lower()


def _signature(component: CashflowComponent) -> tuple[str, ...]:
    direction = "inflow" if component.cash_delta_cent >= 0 else "outflow"
    return (
        component.voucher_date,
        component.voucher_no,
        _normalize_summary(component.summary),
        direction,
        str(abs(component.cash_delta_cent)),
        _normalize_summary(component.original_item_text),
    )


def find_suspected_duplicates(
    components: Sequence[CashflowComponent],
    performance_cent: int,
) -> tuple[DuplicateGroup, ...]:
    grouped: dict[tuple[str, ...], list[CashflowComponent]] = defaultdict(list)
    for component in components:
        grouped[_signature(component)].append(component)

    result: list[DuplicateGroup] = []
    for signature, items in grouped.items():
        pending = list(items)
        while pending:
            first = pending.pop(0)
            occupied_sources = set(first.source_file_ids)
            repeated_items: list[CashflowComponent] = []
            remaining: list[CashflowComponent] = []
            for candidate in pending:
                candidate_sources = set(candidate.source_file_ids)
                if occupied_sources.isdisjoint(candidate_sources):
                    repeated_items.append(candidate)
                    occupied_sources.update(candidate_sources)
                else:
                    remaining.append(candidate)
            pending = remaining
            if not repeated_items:
                continue
            matched_items = (first, *repeated_items)
            amounts = tuple(item.cash_delta_cent for item in matched_items)
            worst_case = sum(abs(item.cash_delta_cent) for item in repeated_items)
            result.append(
                DuplicateGroup(
                    group_id=stable_id(
                        "DUP", *signature, *(item.component_id for item in matched_items)
                    ),
                    component_ids=tuple(item.component_id for item in matched_items),
                    component_amounts_cent=amounts,
                    signature=signature,
                    default_decision="keep",
                    worst_case_impact_cent=worst_case,
                    blocks_manual_completion=worst_case >= performance_cent,
                    item_id=first.original_item_text,
                )
            )
    return tuple(result)


def assign_duplicate_items(
    groups: Sequence[DuplicateGroup],
    decisions: Sequence[ClassificationDecision],
) -> tuple[DuplicateGroup, ...]:
    """把疑似重复组绑定到最终标准项目，避免把客户原标签当作项目编号。"""
    decision_by_component = {item.component_id: item for item in decisions}
    assigned: list[DuplicateGroup] = []
    for group in groups:
        repeated_by_item: dict[str, list[int]] = defaultdict(list)
        for index, component_id in enumerate(group.component_ids[1:], 1):
            decision = decision_by_component.get(component_id)
            repeated_by_item["" if decision is None else decision.system_item_id].append(index)
        if not repeated_by_item or "" in repeated_by_item:
            assigned.append(replace(group, item_id="", blocks_manual_completion=True))
            continue
        for item_id, indices in repeated_by_item.items():
            component_ids = (group.component_ids[0],) + tuple(
                group.component_ids[index] for index in indices
            )
            component_amounts = (group.component_amounts_cent[0],) + tuple(
                group.component_amounts_cent[index] for index in indices
            )
            assigned.append(
                replace(
                    group,
                    group_id=(
                        group.group_id
                        if len(repeated_by_item) == 1
                        else stable_id("DUP", group.group_id, item_id)
                    ),
                    component_ids=component_ids,
                    component_amounts_cent=component_amounts,
                    worst_case_impact_cent=sum(abs(value) for value in component_amounts[1:]),
                    item_id=item_id,
                    baseline_statement_amount_cent=sum(
                        statement_amount_cent(
                            group.component_amounts_cent[index],
                            decision_by_component[group.component_ids[index]].normal_direction,
                        )
                        for index in indices
                    ),
                )
            )
    return tuple(assigned)


def apply_duplicate_decisions(
    groups: Sequence[DuplicateGroup],
    decisions: Mapping[str, str],
) -> tuple[DuplicateAdjustment, ...]:
    adjustments: list[DuplicateAdjustment] = []
    for group in groups:
        decision = decisions.get(group.group_id, group.default_decision)
        if decision == "keep":
            continue
        if decision != "exclude":
            raise ValueError(f"疑似重复组 {group.group_id} 的决定只能是 keep 或 exclude")
        for component_id, amount in zip(
            group.component_ids[1:], group.component_amounts_cent[1:], strict=True
        ):
            adjustments.append(
                DuplicateAdjustment(
                    group.group_id,
                    component_id,
                    -amount,
                    "人工确认跨文件重复，仅形成调整，不删除源业务组成",
                )
            )
    return tuple(adjustments)
