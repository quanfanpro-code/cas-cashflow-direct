from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from cashflow_direct.classification import RulePack, standardize_flow_item
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    NormalizedEntry,
)


_QUALITY_TEXT = {
    0: "无效证据0分",
    10: "弱证据10分",
    25: "中等证据25分",
    45: "强证据45分",
}

_MATERIALITY_TEXT = {
    "M0": "低于明显微小错报临界值",
    "M1": "达到明显微小错报临界值但低于实际执行重要性",
    "M2": "达到实际执行重要性但低于整体重要性",
    "M3": "达到整体重要性",
}


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


def _independent_sources(
    entry: NormalizedEntry,
    component: CashflowComponent,
    decision: ClassificationDecision,
) -> tuple[str, str]:
    sources: list[str] = []
    if decision.summary_quality > 0:
        sources.append(
            f"摘要“{entry.summary or '摘要为空'}”；{_QUALITY_TEXT[decision.summary_quality]}"
        )
    if decision.account_path_quality > 0 and (
        not sources or decision.sources_independent
    ):
        sources.append(
            "完整对方科目路径“"
            + ("、".join(component.counterpart_accounts) or "路径为空")
            + f"”；{_QUALITY_TEXT[decision.account_path_quality]}"
        )
    if not sources:
        return "无有效证据", "无"
    if len(sources) == 1:
        return sources[0], "无"
    return sources[0], sources[1]


def _score_description(decision: ClassificationDecision) -> str:
    summary = _QUALITY_TEXT.get(decision.summary_quality, "质量未记录")
    account = _QUALITY_TEXT.get(decision.account_path_quality, "质量未记录")
    if decision.source_conflict or decision.evidence_score is None:
        return f"摘要为{summary}，完整对方科目路径为{account}；两个来源冲突，不形成可用总分。"
    if decision.summary_quality > 0 and decision.account_path_quality > 0:
        relationship = (
            "两个来源相互独立并共同支持同一项目"
            if decision.sources_independent
            else "两个来源不独立，合计时只按一个来源"
        )
    else:
        relationship = "仅一个有效来源"
    return (
        f"摘要为{summary}，完整对方科目路径为{account}；"
        f"{relationship}，合计{decision.evidence_score}分。"
    )


def _difference_reason(
    decision: ClassificationDecision,
    *,
    multiple: bool,
) -> str:
    if decision.matched_rule_id == "INTERNAL-TRANSFER":
        return "识别为现金及现金等价物内部划转，按现金范围规则自动排除。"
    score = (
        "证据不存在可用合计分"
        if decision.evidence_score is None
        else f"证据得分{decision.evidence_score}分"
    )
    tier = _MATERIALITY_TEXT.get(
        decision.materiality_level,
        "金额档位未记录",
    )
    permission = (
        "规定的AI复核已完成，符合AI确认后修改条件"
        if decision.decision_source.startswith("ai_")
        else "符合自动修改条件"
    )
    reason = f"{score}；金额档位为{tier}；{permission}。"
    if multiple:
        reason += "该原始明细已按金额关系拆分为多个业务组成，本行列示其中一个已确定结果。"
    return reason


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
    candidates: dict[
        str,
        list[tuple[str, str, ClassificationDecision, CashflowComponent]],
    ] = defaultdict(list)

    for component in components:
        decision = decision_by_component.get(component.component_id)
        if (
            decision is None
            or (not decision.resolved and not decision.excluded)
            or decision.decision_source == "manual"
            or decision.materiality_group_confirmation_status
            == "pending_in_final_workbook"
        ):
            continue
        source_entries = tuple(
            entry_by_id[key]
            for key in component.source_keys
            if key in entry_by_id
        )
        matched = (
            tuple(
                entry
                for entry in source_entries
                if _same_original_item(entry, component, rules)
            )
            if component.original_item_text.strip()
            else tuple(
                entry
                for entry in source_entries
                if entry.retained_side != "cash"
                and (
                    not component.counterpart_accounts
                    or entry.account_name in component.counterpart_accounts
                    or entry.account_name in component.original_counterpart_accounts
                )
            )
        )
        if not matched and len(source_entries) == 1:
            matched = source_entries
        final_item = ("", "不进入正表") if decision.excluded else (
            decision.system_item_id,
            decision.system_item_name,
        )
        for entry in matched:
            candidate = (*final_item, decision, component)
            if not any(
                existing[0:2] == final_item
                and existing[2].component_id == decision.component_id
                for existing in candidates[entry.entry_id]
            ):
                candidates[entry.entry_id].append(candidate)

    for entry_id in internal_transfer_entry_ids:
        if entry_id in entry_by_id and entry_by_id[entry_id].original_flow_item:
            decision = ClassificationDecision(
                component_id=entry_id,
                system_item_id="",
                system_item_name="",
                normal_direction="net",
                matched_rule_id="INTERNAL-TRANSFER",
                reason="现金及现金等价物内部划转",
                evidence_level="strong",
                excluded=True,
                decision_action="exclude",
            )
            component = CashflowComponent(
                component_id=entry_id,
                voucher_key=entry_by_id[entry_id].voucher_key,
                summary=entry_by_id[entry_id].summary,
                cash_delta_cent=0,
            )
            candidates[entry_id].append(("", "不进入正表", decision, component))

    rows: list[tuple[tuple[object, ...], dict[str, object]]] = []
    for entry in entries:
        automatic_items = candidates.get(entry.entry_id, ())
        standardized = standardize_flow_item(entry.original_flow_item, rules)
        multiple = len(automatic_items) > 1
        for final_id, final_name, decision, component in automatic_items:
            if standardized is not None and standardized.item_id == final_id:
                continue
            standardized_name = (
                "原项目为空"
                if not entry.original_flow_item.strip()
                else "原项目无法标准化"
                if standardized is None
                else standardized.name
            )
            source1, source2 = _independent_sources(entry, component, decision)
            reason = _difference_reason(
                decision,
                multiple=multiple,
            )
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
                "对方科目": entry.counterpart_name,
                "原项目标准化结果": standardized_name,
                "审定现流表项目": final_name,
                "差异形成原因": reason,
                "打分逻辑描述及打分结果": _score_description(decision),
                "独立来源1": source1,
                "独立来源2": source2,
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
                        final_name,
                    ),
                    row,
                )
            )
    return tuple(row for _, row in sorted(rows, key=lambda item: item[0]))
