from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from cashflow_direct.classification import RulePack, standardize_flow_item
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    NormalizedEntry,
)


def _same_original_item(
    entry: NormalizedEntry,
    component: CashflowComponent,
    rules: RulePack,
) -> bool:
    entry_item = standardize_flow_item(entry.original_flow_item, rules)
    component_item = standardize_flow_item(component.original_item_text, rules)
    if entry_item is not None and component_item is not None:
        return entry_item.item_id == component_item.item_id
    return bool(entry.original_flow_item) and (
        entry.original_flow_item.strip() == component.original_item_text.strip()
    )


def _yuan(value: int | None) -> float | None:
    return None if value is None else value / 100


def build_original_auto_differences(
    entries: Sequence[NormalizedEntry],
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    internal_transfer_entry_ids: frozenset[str],
    rules: RulePack,
    file_name_by_id: Mapping[str, str],
) -> tuple[dict[str, object], ...]:
    entry_by_id = {entry.entry_id: entry for entry in entries}
    decision_by_component = {item.component_id: item for item in decisions}
    candidates: dict[str, list[tuple[str, str]]] = defaultdict(list)

    for component in components:
        decision = decision_by_component.get(component.component_id)
        if decision is None:
            continue
        source_entries = tuple(
            entry_by_id[key]
            for key in component.source_keys
            if key in entry_by_id and entry_by_id[key].original_flow_item
        )
        matched = tuple(
            entry
            for entry in source_entries
            if _same_original_item(entry, component, rules)
        )
        if not matched and len(source_entries) == 1:
            matched = source_entries
        automatic = (
            "",
            "不进入正表",
        ) if decision.excluded or not decision.system_item_id else (
            decision.system_item_id,
            decision.system_item_name,
        )
        for entry in matched:
            if automatic not in candidates[entry.entry_id]:
                candidates[entry.entry_id].append(automatic)

    for entry_id in internal_transfer_entry_ids:
        if entry_id in entry_by_id and entry_by_id[entry_id].original_flow_item:
            automatic = ("", "不进入正表")
            if automatic not in candidates[entry_id]:
                candidates[entry_id].append(automatic)

    rows: list[tuple[tuple[object, ...], dict[str, object]]] = []
    for entry in entries:
        if not entry.original_flow_item:
            continue
        automatic_items = candidates.get(entry.entry_id, ())
        standardized = standardize_flow_item(entry.original_flow_item, rules)
        multiple = len(automatic_items) > 1
        for automatic_id, automatic_name in automatic_items:
            if standardized is not None and standardized.item_id == automatic_id:
                continue
            if standardized is None:
                standardized_name = "原项目无法标准化"
                reason = "原项目无法标准化"
            elif not automatic_id:
                standardized_name = standardized.name
                reason = "自动判定不进入正表"
            else:
                standardized_name = standardized.name
                reason = "标准项目不一致"
            if multiple:
                reason += "；同一原始明细对应多个自动判定结果"
            row = {
                "日期": entry.voucher_date,
                "凭证字": entry.voucher_word,
                "凭证号": entry.voucher_no,
                "摘要": entry.summary,
                "科目编码": entry.account_code,
                "科目名称": entry.account_name,
                "借方": _yuan(entry.source_debit_cent),
                "贷方": _yuan(entry.source_credit_cent),
                "流量金额（原币）": _yuan(entry.source_flow_amount_cent),
                "主表项目名称": entry.original_flow_item,
                "对方科目": entry.counterpart_name,
                "原项目标准化结果": standardized_name,
                "自动判定现流项目": automatic_name,
                "差异说明": reason,
                "来源文件": file_name_by_id.get(
                    entry.source.file_id, entry.source.file_id
                ),
                "来源工作表": entry.source.sheet_name,
                "来源单元格": entry.source.cell_range,
            }
            rows.append(
                (
                    (
                        row["来源文件"],
                        entry.source.sheet_name,
                        entry.source.row_start,
                        automatic_name,
                    ),
                    row,
                )
            )
    return tuple(row for _, row in sorted(rows, key=lambda item: item[0]))
