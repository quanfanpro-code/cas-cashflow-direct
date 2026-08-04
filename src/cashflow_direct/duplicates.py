from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.money import stable_id


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
        if len(items) < 2:
            continue
        source_sets = [frozenset(item.source_file_ids) for item in items]
        if not any(left.isdisjoint(right) for index, left in enumerate(source_sets) for right in source_sets[index + 1 :]):
            continue
        amounts = tuple(item.cash_delta_cent for item in items)
        worst_case = sum(abs(amount) for amount in amounts[1:])
        result.append(
            DuplicateGroup(
                group_id=stable_id("DUP", *signature, *(item.component_id for item in items)),
                component_ids=tuple(item.component_id for item in items),
                component_amounts_cent=amounts,
                signature=signature,
                default_decision="keep",
                worst_case_impact_cent=worst_case,
                blocks_manual_completion=worst_case >= performance_cent,
                item_id=items[0].original_item_text,
            )
        )
    return tuple(result)


def assign_duplicate_items(
    groups: Sequence[DuplicateGroup],
    decisions: Sequence[ClassificationDecision],
) -> tuple[DuplicateGroup, ...]:
    """把疑似重复组绑定到最终标准项目，避免把客户原标签当作项目编号。"""
    item_by_component = {item.component_id: item.system_item_id for item in decisions}
    assigned: list[DuplicateGroup] = []
    for group in groups:
        item_ids = {
            item_by_component[component_id]
            for component_id in group.component_ids
            if item_by_component.get(component_id)
        }
        if len(item_ids) == 1:
            assigned.append(replace(group, item_id=item_ids.pop()))
        else:
            assigned.append(replace(group, item_id="", blocks_manual_completion=True))
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
