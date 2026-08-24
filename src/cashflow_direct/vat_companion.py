from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from cashflow_direct.components import ComponentSourceAllocation
from cashflow_direct.models import CashflowComponent, ClassificationDecision


_VAT_TERMS = ("进项税", "销项税")


@dataclass(frozen=True, slots=True)
class VatCompanionRelation:
    vat_component_id: str
    base_component_id: str
    shared_entry_ids: tuple[str, ...]
    status: str
    reason: str


def _is_vat_component(component: CashflowComponent) -> bool:
    return any(
        term in account
        for account in component.counterpart_accounts
        for term in _VAT_TERMS
    )


def build_vat_companion_relations(
    components: Sequence[CashflowComponent],
    source_allocations: Sequence[ComponentSourceAllocation],
) -> tuple[VatCompanionRelation, ...]:
    allocated_entries: dict[str, set[str]] = {}
    for allocation in source_allocations:
        allocated_entries.setdefault(allocation.component_id, set()).add(
            allocation.entry_id
        )

    relations: list[VatCompanionRelation] = []
    for vat in components:
        if not _is_vat_component(vat):
            continue
        vat_entries = allocated_entries.get(vat.component_id, set())
        matches = []
        for base in components:
            if (
                base.component_id == vat.component_id
                or base.voucher_key != vat.voucher_key
                or _is_vat_component(base)
                or (base.cash_delta_cent > 0) != (vat.cash_delta_cent > 0)
            ):
                continue
            shared = tuple(
                sorted(vat_entries.intersection(allocated_entries.get(base.component_id, set())))
            )
            if shared:
                matches.append((base, shared))
        if len(matches) == 1:
            base, shared = matches[0]
            relations.append(
                VatCompanionRelation(
                    vat.component_id,
                    base.component_id,
                    shared,
                    "unique",
                    "同凭证、同方向且共用唯一现金来源",
                )
            )
        elif matches:
            relations.append(
                VatCompanionRelation(
                    vat.component_id,
                    "",
                    tuple(sorted({entry for _, shared in matches for entry in shared})),
                    "conflict",
                    "同一增值税组成对应多个基础交易",
                )
            )
        else:
            relations.append(
                VatCompanionRelation(
                    vat.component_id,
                    "",
                    (),
                    "missing",
                    "未找到共用现金来源的唯一基础交易",
                )
            )
    return tuple(relations)


def _base_reason(reason: str) -> str:
    return reason.split("；增值税附属关系：", 1)[0].removesuffix(
        "；进项税或销项税缺少同一现金业务内已识别的基础交易，不能据此修改原项目"
    )


def apply_vat_companion_relations(
    decisions: Sequence[ClassificationDecision],
    relations: Sequence[VatCompanionRelation],
) -> tuple[ClassificationDecision, ...]:
    decision_by_id = {decision.component_id: decision for decision in decisions}
    relation_by_id = {relation.vat_component_id: relation for relation in relations}
    refreshed: list[ClassificationDecision] = []
    for decision in decisions:
        relation = relation_by_id.get(decision.component_id)
        if relation is None:
            refreshed.append(decision)
            continue
        reason = _base_reason(decision.reason)
        if relation.status != "unique":
            refreshed.append(
                replace(
                    decision,
                    vat_base_component_id="",
                    vat_relation_status=relation.status,
                    vat_base_missing=True,
                    reason=f"{reason}；增值税附属关系：{relation.reason}",
                )
            )
            continue
        base = decision_by_id[relation.base_component_id]
        base_is_settled = bool(
            base.resolved and (base.excluded or base.system_item_id)
        )
        base_has_project = base_is_settled and not base.excluded
        if base_has_project:
            follow_reason = f"跟随基础项目{base.system_item_name}"
        elif base_is_settled:
            follow_reason = "跟随基础项目明确排除"
        else:
            follow_reason = "基础项目尚未落定，等待同一决定"
        refreshed.append(
            replace(
                decision,
                system_item_id=(
                    base.system_item_id if base_is_settled else decision.system_item_id
                ),
                system_item_name=(
                    base.system_item_name
                    if base_is_settled
                    else decision.system_item_name
                ),
                normal_direction=(
                    base.normal_direction if base_is_settled else decision.normal_direction
                ),
                resolved=base_is_settled,
                excluded=base.excluded if base_is_settled else False,
                decision_source="vat_companion",
                decision_action="vat_follow_base",
                vat_base_missing=False,
                vat_base_component_id=base.component_id,
                vat_relation_status="unique",
                reason=(
                    f"{reason}；增值税附属关系：与基础组成{base.component_id}"
                    f"共用现金来源{'、'.join(relation.shared_entry_ids)}；{follow_reason}"
                ),
            )
        )
    return tuple(refreshed)
