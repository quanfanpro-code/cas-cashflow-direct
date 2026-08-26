from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence

from cashflow_direct.classification import RulePack, standardize_flow_item
from cashflow_direct.components import (
    ComponentSourceAllocation,
    _account_key,
    flow_direction_source,
)
from cashflow_direct.evidence import split_account_levels
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    NormalizedEntry,
)
from cashflow_direct.rule_registry import default_rule_registry


_TRACE_RULES = default_rule_registry()
_QUALITY_TEXT = {
    int(score): label
    for score, label in _TRACE_RULES.output_policy["quality_labels"]["trace"].items()
}

_ACTION_TEXT = dict(
    _TRACE_RULES.evidence_policy["action_display_labels"]
)

_MATERIALITY_TEXT = dict(_TRACE_RULES.output_policy["materiality_labels"])

_ANOMALY_TEXT = {
    "summary_empty": "摘要为空，输入非法",
    "summary_allocation_ambiguous": "同凭证摘要分配存在多种合理组合",
    "account_path_empty": "完整对方科目路径为空，输入非法",
    "account_path_invalid": "完整对方科目路径无效，输入非法",
    "voucher_unbalanced": "凭证借贷不平衡",
    "unallocated_cash": "现金金额尚未完整分配",
    "cash_allocation_mismatch": "现金分配金额不守恒",
}


def _unique_text(values: Sequence[object], empty: str = "未记录") -> str:
    result = tuple(
        dict.fromkeys(str(value) for value in values if value not in (None, ""))
    )
    return "、".join(result) if result else empty


def _forced_check(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
) -> str:
    anomalies = {
        anomaly for component in components for anomaly in component.anomalies
    }
    if {"summary_empty", "account_path_empty", "account_path_invalid"}.intersection(
        anomalies
    ):
        return "输入非法，已隔离"
    if any(item.source_conflict for item in decisions):
        return "两个来源冲突"
    if any(item.business_conflict for item in decisions):
        return "业务事实冲突"
    if any(item.company_rule_conflict for item in decisions):
        return "公司规则冲突"
    if any(item.vat_base_missing for item in decisions):
        return "增值税基础交易缺失"
    if any(item.net_item_facts_missing for item in decisions):
        return "净额列报事实缺失"
    if any(item.individual_tax_fact_missing for item in decisions):
        return "个税服务对象不明"
    return ""


def _scope_status(account_name: str, state: Mapping[str, object]) -> str:
    scope = state.get("cash_scope", {})
    included = set(scope.get("included_keys", ()))
    excluded = set(scope.get("excluded_keys", ()))
    key = _account_key(account_name)
    for mapped_key, original_names in scope.get("account_names_by_key", ()):
        if account_name in original_names:
            key = str(mapped_key)
            break
    if key in included:
        return "现金及现金等价物范围内"
    if key in excluded:
        return "现金及现金等价物范围外"
    return "非现金账户"


def _quality_description(decision: ClassificationDecision | None) -> str:
    if decision is None:
        return "系统未记录证据质量"
    summary = _QUALITY_TEXT.get(decision.summary_quality, "摘要质量未记录")
    account = _QUALITY_TEXT.get(decision.account_path_quality, "路径质量未记录")
    if decision.source_conflict or decision.evidence_score is None:
        return f"摘要{summary}、路径{account}；两个来源互相冲突，不形成可用总分"
    independence = "两个来源独立" if decision.sources_independent else "两个来源不独立，合计时只按一个来源"
    return f"摘要{summary}、路径{account}；{independence}；总分{decision.evidence_score}分"


def _ai_process(
    records: Sequence[Mapping[str, object]], rules: RulePack
) -> str:
    if not records:
        return "未经过AI复核"
    parts: list[str] = []
    previous_candidates: tuple[str, str] | None = None
    for index, record in enumerate(records, 1):
        summary = record.get("summary") if isinstance(record.get("summary"), Mapping) else {}
        account = record.get("account_path") if isinstance(record.get("account_path"), Mapping) else {}
        def candidate_names(source: Mapping[str, object]) -> str:
            item_ids = tuple(
                str(value) for value in source.get("candidate_item_ids", ())
            ) or ((str(source.get("candidate_item_id", "")),) if source.get("candidate_item_id") else ())
            names = tuple(
                rules.item_by_id[item_id].name
                for item_id in item_ids
                if item_id in rules.item_by_id
            )
            return "、".join(names) if names else "未形成候选"
        candidates = (
            candidate_names(summary),
            candidate_names(account),
        )
        change = (
            "首轮意见"
            if previous_candidates is None
            else "与上一轮一致"
            if candidates == previous_candidates
            else f"较上一轮变化为摘要支持{candidates[0]}、路径支持{candidates[1]}"
        )
        parts.append(
            f"第{index}次AI（轮次{record.get('review_round', index)}；"
            f"任务{record.get('task_id', '未记录')}；"
            f"执行者{record.get('reviewer_id', '未记录')}；"
            f"模型{record.get('model_id', '未记录')}；"
            f"时间{record.get('reviewed_at', '未记录')}）："
            f"摘要支持{candidates[0]}，摘要依据{summary.get('basis_text', '未记录')}；"
            f"路径支持{candidates[1]}，路径依据{account.get('basis_text', '未记录')}；"
            f"{record.get('reason', '理由未记录')}；{change}"
        )
        previous_candidates = candidates
    return "；".join(parts)


def _source_amount(
    entries: Sequence[NormalizedEntry],
    source_field: str,
    normalized_field: str,
) -> float | str:
    """优先展示原表金额；原表未单独留值时才回退到规范化后的非零金额。"""
    source_values = [
        getattr(entry, source_field)
        for entry in entries
        if getattr(entry, source_field) is not None
    ]
    if source_values:
        return sum(int(value) for value in source_values) / 100
    normalized_values = [
        int(getattr(entry, normalized_field))
        for entry in entries
        if int(getattr(entry, normalized_field)) != 0
    ]
    return sum(normalized_values) / 100 if normalized_values else "未记录"


def build_trace_rows(
    entries: Sequence[NormalizedEntry],
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    allocations: Sequence[ComponentSourceAllocation],
    materiality_assessments: Sequence[Mapping[str, object]],
    rules: RulePack,
    state: Mapping[str, object],
    file_name_by_id: Mapping[str, str],
) -> tuple[dict[str, object], ...]:
    if any(item.account_mapping_status != "confirmed" for item in components):
        raise ValueError("一级科目映射未全部确认，不能生成分类留痕")
    entry_by_id = {entry.entry_id: entry for entry in entries}
    decision_by_component = {item.component_id: item for item in decisions}
    materiality_by_component = {
        str(item.get("record_id", "")): item
        for item in materiality_assessments
    }
    allocations_by_component: dict[str, list[ComponentSourceAllocation]] = defaultdict(list)
    for allocation in allocations:
        allocations_by_component[allocation.component_id].append(allocation)
    ai_by_component: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for item in state.get("structured_ai_validation", {}).get("valid_results", ()):
        ai_by_component[str(item.get("component_id", ""))].append(item)
    versions = state.get("versions", {})
    level1_mapping_by_original = {
        str(item.get("original_level1", "")): item
        for item in state.get("account_mapping_records", ())
    }
    rows: list[dict[str, object]] = []
    for component in components:
        decision = decision_by_component.get(component.component_id)
        if decision is not None and decision.excluded:
            continue
        related_decisions = () if decision is None else (decision,)
        assessment = materiality_by_component.get(component.component_id, {})
        source_entries = tuple(
            entry_by_id[key] for key in component.source_keys if key in entry_by_id
        )
        original = standardize_flow_item(component.original_item_text, rules)
        original_result = (
            "原项目为空"
            if not component.original_item_text.strip()
            else original.name
            if original is not None
            else "原项目无法标准化"
        )
        counterpart_path = _unique_text(
            component.counterpart_accounts,
            "完整对方科目路径为空",
        )
        mapping_status = "已确认"
        candidates = _unique_text(
            tuple(
                rules.item_by_id[item_id].name
                for decision in related_decisions
                for item_id in decision.candidate_item_ids
                if item_id in rules.item_by_id
            ),
            "未形成候选",
        )
        final_items = [
            "明确排除"
            if decision.excluded
            else decision.system_item_name
            if decision.resolved and decision.system_item_id
            else "等待人工复核"
            for decision in related_decisions
        ]
        ai_records = tuple(
            item
            for item in ai_by_component.get(component.component_id, ())
        )
        primary_decision = related_decisions[0] if related_decisions else None
        ai_process = _ai_process(ai_records, rules)
        action_text = (
            _ACTION_TEXT.get(primary_decision.decision_action, "系统动作未记录")
            if primary_decision is not None
            else "系统动作未记录"
        )
        anomalies = list(
            dict.fromkeys(
                _ANOMALY_TEXT.get(anomaly, anomaly) for anomaly in component.anomalies
            )
        )
        if primary_decision is not None and primary_decision.source_conflict:
            anomalies.append("两个来源冲突")
        if primary_decision is not None and primary_decision.business_conflict:
            anomalies.append("业务事实冲突")
        if primary_decision is not None and primary_decision.direction_status == "incompatible":
            anomalies.append("现金方向与候选项目不相容（仅异常线索）")
        anomaly_text = "、".join(dict.fromkeys(anomalies)) if anomalies else "未发现异常"
        if primary_decision is None:
            current_process = "系统尚未形成候选，等待人工复核。"
        elif primary_decision.resolved or primary_decision.excluded:
            ai_note = "；所需AI复核已完成" if ai_records else ""
            current_process = f"{action_text}{ai_note}，已形成最终处理结果。"
        else:
            current_process = "所需AI复核已完成后仍无法确定，等待人工复核。"
        review_status = (
            "已明确排除"
            if primary_decision is not None and primary_decision.excluded
            else "已形成最终决定"
            if primary_decision is not None and primary_decision.resolved
            else "等待人工决定"
            if primary_decision is not None
            and primary_decision.decision_action == "human_decision"
            else "等待人工复核"
        )
        component_allocations = tuple(allocations_by_component.get(component.component_id, ()))
        allocation_by_entry = {
            item.entry_id: item for item in component_allocations
        }
        cash_entries = tuple(
            entry_by_id[item.entry_id]
            for item in component_allocations
            if item.entry_id in entry_by_id
        )
        row_entries: tuple[NormalizedEntry | None, ...] = (
            cash_entries
            or tuple(entry for entry in source_entries if entry.retained_side == "cash")
            or (None,)
        )
        for representative in row_entries:
            allocation = (
                None
                if representative is None
                else allocation_by_entry.get(representative.entry_id)
            )
            cash_entry = (
                representative
                if allocation is not None
                else next(iter(cash_entries), None)
            )
            cash_account = (
                "" if representative is None else representative.account_name
            )
            if (
                not cash_account
                and representative is not None
                and _scope_status(representative.account_name, state)
                == "现金及现金等价物范围内"
            ):
                cash_account = representative.account_name
            source_account = (
                "未提供" if representative is None else representative.account_name
            )
            source_summary = (
                "摘要为空（非法输入）"
                if representative is None or not representative.summary.strip()
                else representative.summary
            )
            source_path = source_account
            source_levels = split_account_levels(source_path)
            raw_counterpart_paths = (
                component.original_counterpart_accounts
                or component.counterpart_accounts
            )
            raw_counterpart_levels = tuple(
                split_account_levels(path)
                for path in raw_counterpart_paths
                if path.strip()
            )
            standard_counterpart_levels = tuple(
                split_account_levels(path)
                for path in component.counterpart_accounts
                if path.strip()
            )
            row_original_level1 = _unique_text(
                tuple(levels[0] for levels in raw_counterpart_levels if levels)
            )
            row_standard_level1 = _unique_text(
                tuple(levels[0] for levels in standard_counterpart_levels if levels),
                "待确认",
            )
            row_middle_levels = _unique_text(
                tuple(
                    "_".join(levels[1:-1])
                    for levels in raw_counterpart_levels
                    if len(levels) > 2
                ),
                "无",
            )
            row_leaf_level = _unique_text(
                tuple(levels[-1] for levels in raw_counterpart_levels if levels)
            )
            row_mapping_records = tuple(
                level1_mapping_by_original[levels[0]]
                for levels in raw_counterpart_levels
                if levels and levels[0] in level1_mapping_by_original
            )
            row_mapping_candidates = _unique_text(
                tuple(
                    candidate
                    for item in row_mapping_records
                    for candidate in item.get("candidate_standard_names", ())
                ),
                "无自动候选",
            )
            row_mapping_basis = _unique_text(
                tuple(item.get("basis", "") for item in row_mapping_records),
                "未记录",
            )
            if representative is None:
                row_counterpart_path = "完整对方科目路径为空"
            elif representative.retained_side == "cash" and component.counterpart_accounts:
                row_counterpart_path = counterpart_path
            elif representative.counterpart_name.strip():
                row_counterpart_path = representative.counterpart_name
            else:
                row_counterpart_path = cash_account or "完整对方科目路径为空"
            source_debit = _source_amount(
                () if representative is None else (representative,),
                "source_debit_cent",
                "debit_cent",
            )
            source_credit = _source_amount(
                () if representative is None else (representative,),
                "source_credit_cent",
                "credit_cent",
            )
            source_flow = _source_amount(
                () if representative is None else (representative,),
                "source_flow_amount_cent",
                "flow_amount_cent",
            )
            rows.append(
                {
                "日期": "" if representative is None else representative.voucher_date,
                "凭证字": "" if representative is None else representative.voucher_word,
                "凭证号": "" if representative is None else representative.voucher_no,
                "本行摘要": source_summary,
                "本行科目路径": source_account,
                "原始一级科目": row_original_level1,
                "原始科目编码": "" if representative is None else representative.account_code,
                "原始完整科目路径": source_path,
                "本行完整对方科目路径": row_counterpart_path,
                "标准一级科目": row_standard_level1,
                "中间层级": row_middle_levels,
                "末级明细": row_leaf_level,
                "映射状态": mapping_status,
                "一级科目映射候选": row_mapping_candidates,
                "一级科目映射依据": row_mapping_basis,
                "现金账户路径": cash_account or "未记录",
                "现金账户范围状态": (
                    _scope_status(cash_account, state) if cash_account else "未记录"
                ),
                "对方科目范围状态": _unique_text(
                    (_scope_status(row_counterpart_path, state),)
                ),
                "现金方向依据": _unique_text(
                    ()
                    if representative is None
                    else (flow_direction_source(representative),)
                ),
                "借方": source_debit,
                "贷方": source_credit,
                "流量金额（原币）": source_flow,
                "原现流项目": component.original_item_text or "原项目为空",
                "原项目标准化结果": original_result,
                "系统候选项目": candidates,
                "判断理由": (
                    primary_decision.reason
                    if primary_decision is not None and primary_decision.reason
                    else "系统未记录判断理由"
                ),
                "摘要来源质量": _unique_text(
                    tuple(
                        _QUALITY_TEXT.get(decision.summary_quality, "未记录")
                        for decision in related_decisions
                    )
                ),
                "完整路径来源质量": _unique_text(
                    tuple(
                        _QUALITY_TEXT.get(decision.account_path_quality, "未记录")
                        for decision in related_decisions
                    )
                ),
                "两个来源是否独立": _unique_text(
                    tuple(
                        "是" if decision.sources_independent else "否"
                        for decision in related_decisions
                    )
                ),
                "证据质量说明": _quality_description(primary_decision),
                "证据得分": _unique_text(
                    tuple(
                        "来源冲突，无可用总分"
                        if decision.evidence_score is None
                        else decision.evidence_score
                        for decision in related_decisions
                    )
                ),
                "单笔金额": (
                    int(assessment.get("single_amount_cent", 0)) / 100
                    if assessment
                    else ""
                ),
                "单笔重要性层级": _MATERIALITY_TEXT.get(
                    primary_decision.single_materiality_level,
                    primary_decision.single_materiality_level,
                ) if primary_decision is not None else "",
                "强制检查": _forced_check((component,), related_decisions),
                "唯一动作": action_text,
                "异常": anomaly_text,
                "AI复核过程": ai_process,
                "当前决定形成过程": current_process,
                "最终决定项目": _unique_text(tuple(final_items), "尚未决定"),
                "复核状态": review_status,
                "本行分配现金变化": (
                    0 if allocation is None else allocation.allocated_cent / 100
                ),
                "组成明细": (
                    "非现金分录，不重复分配现金变化"
                    if allocation is None
                    else f"{cash_account or '现金账户未记录'}"
                    f"（{representative.source.cell_range if representative is not None else '来源未记录'}）："
                    f"{allocation.allocated_cent / 100:.2f}元"
                ),
                "评分版本": str(versions.get("scoring", "未记录")),
                "动作表版本": str(versions.get("action_matrix", "未记录")),
                "决策规则编号(技术)": (
                    primary_decision.decision_rule_id
                    if primary_decision is not None
                    else "未记录"
                ),
                "规则中心版本": str(versions.get("rule_center", "未记录")),
                "AI复核记录(技术)": (
                    json.dumps(ai_records, ensure_ascii=False) if ai_records else ""
                ),
                "来源文件": (
                    "未记录"
                    if representative is None
                    else file_name_by_id.get(
                        representative.source.file_id,
                        representative.source.file_id,
                    )
                ),
                "来源工作表": (
                    "未记录" if representative is None else representative.source.sheet_name
                ),
                "来源行号": (
                    "未记录" if representative is None else representative.source.row_start
                ),
                "来源单元格": (
                    "未记录" if representative is None else representative.source.cell_range
                ),
                "原始行编号(技术)": (
                    "" if representative is None else representative.entry_id
                ),
                "业务组成编号(技术)": component.component_id,
                }
            )
    return tuple(rows)
