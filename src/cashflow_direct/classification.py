from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from cashflow_direct.account_dictionary import score_dictionary_hits, score_summary_hit
from cashflow_direct.evidence import (
    SOURCE_ACCOUNT_PATH,
    SOURCE_SUMMARY,
    aggregate_evidence,
    score_rule,
    split_account_levels,
)
from cashflow_direct.decision_policy import (
    DecisionAction,
    MaterialityLevel,
    OriginalItemState,
    route_decision,
)
from cashflow_direct.consistency import find_consistency_groups
from cashflow_direct.materiality import (
    MaterialityAssessment,
    MaterialityRecord,
    assess_materiality_records,
)
from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.money import stable_id

# 来源中文名映射（用于 reason 展示，最终 xlsx 人类可读列一律中文）
_SOURCE_CN = {
    SOURCE_SUMMARY: "摘要",
    SOURCE_ACCOUNT_PATH: "完整对方科目路径",
}


@dataclass(frozen=True, slots=True)
class StatementItem:
    item_id: str
    name: str
    section: str
    display_order: int
    is_leaf: bool
    normal_direction: str
    formula_components: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ClassificationRule:
    rule_id: str
    item_id: str
    priority: int
    direction: str
    summary_terms: tuple[str, ...]
    account_terms: tuple[str, ...]
    exclude_terms: tuple[str, ...]
    account_exclude_terms: tuple[str, ...]
    evidence_level: str
    sole_account_terms: tuple[str, ...] = ()
    # 为 True 时规则必须命中对方科目才参与打分（用于按服务对象分流的职工类规则）
    require_account: bool = False


@dataclass(frozen=True, slots=True)
class RulePack:
    statement_items: tuple[StatementItem, ...]
    rules: tuple[ClassificationRule, ...]

    @property
    def item_by_id(self) -> dict[str, StatementItem]:
        return {item.item_id: item for item in self.statement_items}


@dataclass(frozen=True, slots=True)
class ClassificationRoutingResult:
    decisions: tuple[ClassificationDecision, ...]
    ai_tasks: tuple[object, ...]
    materiality_assessments: tuple[MaterialityAssessment, ...]


def _read_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8-sig") as source:
        return json.load(source)


def load_rule_pack(root: Path) -> RulePack:
    reference_root = Path(root) / "references"
    item_payload = _read_json(reference_root / "一般企业正表项目.json")
    rule_payload = _read_json(reference_root / "直接法分类规则.json")
    items = tuple(
        StatementItem(
            item_id=item["item_id"],
            name=item["name"],
            section=item["section"],
            display_order=int(item["display_order"]),
            is_leaf=bool(item["is_leaf"]),
            normal_direction=item["normal_direction"],
            formula_components=tuple((part[0], int(part[1])) for part in item["formula_components"]),
        )
        for item in item_payload["statement_items"]
    )
    rules = tuple(
        ClassificationRule(
            rule_id=rule["rule_id"],
            item_id=rule["item_id"],
            priority=int(rule["priority"]),
            direction=rule["direction"],
            summary_terms=tuple(rule["summary_terms"]),
            account_terms=tuple(rule["account_terms"]),
            exclude_terms=tuple(rule["exclude_terms"]),
            account_exclude_terms=tuple(rule.get("account_exclude_terms", ())),
            evidence_level=rule["evidence_level"],
            sole_account_terms=tuple(rule.get("sole_account_terms", ())),
            require_account=bool(rule.get("require_account", False)),
        )
        for rule in rule_payload["rules"]
    )
    if len(items) != 35 or len({item.item_id for item in items}) != 35:
        raise ValueError("一般企业正表项目必须恰好包含 35 个唯一行项目")
    item_ids = {item.item_id for item in items}
    if any(rule.item_id not in item_ids for rule in rules):
        raise ValueError("分类规则引用了不存在的正表项目")
    return RulePack(items, tuple(sorted(rules, key=lambda item: (item.priority, item.rule_id))))


def _matched_terms(
    rule: ClassificationRule,
    component: CashflowComponent,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    summary_text = component.summary
    account_text = "|".join(component.counterpart_accounts)
    summary_hits = tuple(
        dict.fromkeys(
            term
            for term in (*rule.summary_terms, *rule.account_terms)
            if term in summary_text
        )
    )
    account_hits = tuple(
        term
        for term in (*rule.account_terms, *rule.sole_account_terms)
        if term in account_text
    )
    return summary_hits, account_hits


def _rule_matches(rule: ClassificationRule, component: CashflowComponent) -> bool:
    direction = "inflow" if component.cash_delta_cent > 0 else "outflow"
    if rule.direction not in {"any", direction}:
        return False
    account_text = "|".join(component.counterpart_accounts)
    if any(term in component.summary for term in rule.exclude_terms):
        return False
    if any(term in account_text for term in rule.account_exclude_terms):
        return False
    if rule.sole_account_terms:
        counterpart = component.counterpart_accounts
        if not counterpart:
            return False
        # 唯一对方科目判断：每个对方科目名称只需包含任一 sole 词，
        # 以兼容“应交税费_应交个人所得税”这类带明细级次的科目名
        return all(
            any(sole_term in account for sole_term in rule.sole_account_terms)
            for account in counterpart
        )
    if not rule.summary_terms and not rule.account_terms:
        return True
    summary_hits, account_hits = _matched_terms(rule, component)
    return bool(summary_hits or account_hits)


def _business_reason(
    rule: ClassificationRule,
    component: CashflowComponent,
    item: StatementItem,
) -> str:
    summary_hits, account_hits = _matched_terms(rule, component)
    parts = []
    if summary_hits:
        parts.append(f"摘要包含“{'、'.join(summary_hits)}”")
    if account_hits:
        parts.append(f"对方科目包含“{'、'.join(account_hits)}”")
    parts.append(f"现金为{'流入' if component.cash_delta_cent > 0 else '流出'}")
    parts.append(f"因此判断为“{item.name}”")
    return "；".join(parts)


def _normalize_item_name(value: str) -> str:
    return re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】]+", "", value)


def standardize_flow_item(value: str, rules: RulePack) -> StatementItem | None:
    normalized = _normalize_item_name(value)
    return next(
        (
            item
            for item in rules.statement_items
            if item.is_leaf and normalized and _normalize_item_name(item.name) == normalized
        ),
        None,
    )


def classify_component(
    component: CashflowComponent,
    rules: RulePack,
    dictionary: object | None = None,
    summary_dictionary: object | None = None,
) -> ClassificationDecision:
    if component.account_mapping_status != "confirmed":
        raise ValueError("一级科目映射未全部确认，不能进入现金流分类")
    exact_item = standardize_flow_item(component.original_item_text, rules)
    if not component.original_item_text.strip():
        pending_original_state = "blank"
    elif exact_item is None:
        pending_original_state = "unstandardizable"
    else:
        pending_original_state = "pending_comparison"
    illegal_markers = {"summary_empty", "account_path_empty", "account_path_invalid"}
    if illegal_markers.intersection(component.anomalies):
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id="",
            system_item_name="",
            normal_direction="net",
            matched_rule_id="INVALID-INPUT",
            reason="摘要或完整对方科目路径非法，本行业务保留并隔离，补充资料或人工决定前不得进入最终表",
            evidence_level="invalid",
            decision_source="candidate",
            resolved=False,
            evidence_score=0,
            candidate_item_ids=(),
            original_item_state=pending_original_state,
            decision_action="isolate_invalid_input",
            candidate_status="invalid_input",
            original_standard_item_id="" if exact_item is None else exact_item.item_id,
        )
    if component.cash_delta_cent == 0 or any(
        marker in component.anomalies for marker in ("internal_transfer", "non_cash")
    ):
        return ClassificationDecision(
            component.component_id,
            "",
            "",
            "net",
            "EXCLUDED",
            "内部划转、非现金或零金额事项不进入正表",
            "high",
            excluded=True,
            evidence_score=None,
            decision_action="exclude",
        )

    evidence_component = component
    matches = [rule for rule in rules.rules if _rule_matches(rule, evidence_component)]
    business_matches = [
        rule
        for rule in matches
        if rule.summary_terms or rule.account_terms or rule.sole_account_terms
    ]
    structured_semantics_ready = dictionary is not None and summary_dictionary is not None
    if structured_semantics_ready:
        # 正式运行已经完成“完整路径语义确认 + 摘要语义确认”后，只使用这两类
        # 结构化证据。旧关键词规则仅保留给尚未迁移的兼容场景，不能再次参与
        # 正式评分，否则会把同一文本机械拆成额外候选并制造虚假冲突。
        business_matches = []
    dictionary_scores = (
        list(score_dictionary_hits(evidence_component, dictionary))
        if dictionary is not None
        else []
    )
    non_vat_path_candidates = {
        candidate_id
        for score in dictionary_scores
        if not any(
            term in account
            for account in score.account_hits
            for term in ("进项税", "销项税")
        )
        for candidate_id in (score.candidate_item_ids or (score.item_id,))
        if candidate_id
    }
    path_business_conflict = len(non_vat_path_candidates) > 1
    summary_score = (
        score_summary_hit(evidence_component, summary_dictionary)
        if summary_dictionary is not None
        else None
    )
    if not business_matches and not dictionary_scores and summary_score is None:
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id="",
            system_item_name="",
            normal_direction="net",
            matched_rule_id="NO-BUSINESS-CANDIDATE",
            reason="摘要和完整对方科目路径没有形成有效候选；原项目只用于比较，不作为分类证据",
            evidence_level="invalid",
            decision_source="candidate",
            resolved=False,
            evidence_score=0,
            candidate_item_ids=(),
            original_item_state=pending_original_state,
            candidate_status="no_candidate",
            original_standard_item_id="" if exact_item is None else exact_item.item_id,
        )

    rule_scores = [
        score_rule(rule, evidence_component, rules.item_by_id[rule.item_id].normal_direction)
        for rule in business_matches
    ]
    rule_scores.extend(dictionary_scores)
    if summary_score is not None:
        rule_scores.append(summary_score)
    agg = aggregate_evidence(
        rule_scores,
        sources_independent=(
            False if "path_depends_on_summary" in component.anomalies else None
        ),
    )
    if agg is None:
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id="",
            system_item_name="",
            normal_direction="net",
            matched_rule_id="NO-BUSINESS-CANDIDATE",
            reason="摘要和完整对方科目路径没有形成有效候选；原项目只用于比较，不作为分类证据",
            evidence_level="invalid",
            decision_source="candidate",
            resolved=False,
            evidence_score=0,
            candidate_item_ids=(),
            original_item_state=pending_original_state,
            candidate_status="no_candidate",
            original_standard_item_id="" if exact_item is None else exact_item.item_id,
        )

    if not agg.item_id and not agg.conflict and agg.candidate_item_ids:
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id="",
            system_item_name="",
            normal_direction="net",
            matched_rule_id="AMBIGUOUS-SOURCE-CANDIDATES",
            reason=(
                "现有来源只能形成多个合理候选，尚不能唯一判断；"
                "保留候选集合并按证据分数进入后续复核"
            ),
            evidence_level=agg.tier,
            decision_source="candidate",
            resolved=False,
            evidence_score=agg.score,
            evidence_sources=agg.sources,
            candidate_item_ids=agg.candidate_item_ids,
            summary_candidate_item_ids=agg.summary_candidate_item_ids,
            account_path_candidate_item_ids=agg.account_path_candidate_item_ids,
            original_item_state=pending_original_state,
            summary_quality=agg.summary_quality,
            account_path_quality=agg.account_path_quality,
            sources_independent=agg.sources_independent,
            source_conflict=False,
            business_conflict=path_business_conflict,
            candidate_status="ambiguous",
            original_standard_item_id="" if exact_item is None else exact_item.item_id,
        )

    rule_by_id = {rule.rule_id: rule for rule in business_matches}
    best_score = min(
        (
            score
            for score in agg.rule_scores
            if score.item_id == agg.item_id
            or agg.item_id in score.candidate_item_ids
        ),
        key=lambda item: (-item.score, item.priority, item.rule_id),
    )
    semantic_scores = tuple(
        score
        for score in agg.rule_scores
        if (
            score.item_id == agg.item_id
            or agg.item_id in score.candidate_item_ids
        )
        and (score.business_object or score.purpose)
    )
    business_object = "、".join(
        sorted({score.business_object for score in semantic_scores if score.business_object})
    )
    purpose = "、".join(
        sorted({score.purpose for score in semantic_scores if score.purpose})
    )
    item = rules.item_by_id[agg.item_id]
    chosen_rule = rule_by_id.get(best_score.rule_id)
    if chosen_rule is not None:
        reason = _business_reason(chosen_rule, evidence_component, item)
    else:
        # 词典命中的完整路径语义没有 rule JSON 实体，命中内容即对方科目完整路径
        account_text = "、".join(best_score.account_hits) if best_score.account_hits else ""
        reason = (
            f"对方科目包含“{account_text}”，符合“{item.name}”的科目语义词典定义"
        )
        if best_score.note_id:
            # 复核修复：公司特殊规则命中必须留 NOTE 编号痕迹，保证理由可追查
            reason += f"；依据公司特殊规则：{best_score.note_id}"
    score_text = "无可用合计分" if agg.score is None else f"证据得分{agg.score}"
    reason += f"；{score_text}（{'/'.join(_SOURCE_CN[source] for source in agg.sources)}）"
    if path_business_conflict:
        reason += "；多个非增值税业务路径指向不同项目，不允许其中一个项目向其他分录传递"
    evidence_level = agg.tier
    excluded_conflict_rule_ids = tuple(
        score.rule_id for score in agg.rule_scores if score.item_id != agg.item_id
    )
    matched_rule_id = best_score.rule_id

    if agg.conflict:
        conflict_names = "、".join(
            f"“{rules.item_by_id[item_id].name}”" for item_id in agg.conflict_item_ids
        )
        reason += f"；其他证据同时指向{conflict_names}，存在冲突"
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=agg.item_id,
            system_item_name=item.name,
            normal_direction=item.normal_direction,
            matched_rule_id="BUSINESS-RULE-CONFLICT",
            reason=reason,
            evidence_level=evidence_level,
            excluded_conflict_rule_ids=excluded_conflict_rule_ids,
            decision_source="candidate",
            resolved=False,
            evidence_score=None,
            evidence_sources=agg.sources,
            candidate_item_ids=agg.conflict_item_ids,
            summary_candidate_item_ids=agg.summary_candidate_item_ids,
            account_path_candidate_item_ids=agg.account_path_candidate_item_ids,
            original_item_state=pending_original_state,
            summary_quality=agg.summary_quality,
            account_path_quality=agg.account_path_quality,
            sources_independent=agg.sources_independent,
            source_conflict=True,
            business_conflict=path_business_conflict,
            business_object=business_object,
            purpose=purpose,
            candidate_status="source_conflict",
            original_standard_item_id="" if exact_item is None else exact_item.item_id,
        )

    if not component.original_item_text.strip():
        original_state = "blank"
    elif exact_item is None:
        original_state = "unstandardizable"
    elif exact_item.item_id == agg.item_id:
        original_state = "agrees"
        reason += "；原项目能够标准化并与候选一致"
    else:
        original_state = "conflicts"
        reason += f"；原项目“{exact_item.name}”与候选不一致，等待统一动作表决定"
    return ClassificationDecision(
        component_id=component.component_id,
        system_item_id=agg.item_id,
        system_item_name=item.name,
        normal_direction=item.normal_direction,
        matched_rule_id=matched_rule_id,
        reason=reason,
        evidence_level=evidence_level,
        excluded_conflict_rule_ids=excluded_conflict_rule_ids,
        decision_source="candidate",
        resolved=False,
        evidence_score=agg.score,
        evidence_sources=agg.sources,
        candidate_item_ids=(agg.item_id,),
        summary_candidate_item_ids=agg.summary_candidate_item_ids,
        account_path_candidate_item_ids=agg.account_path_candidate_item_ids,
        original_item_state=original_state,
        summary_quality=agg.summary_quality,
        account_path_quality=agg.account_path_quality,
        sources_independent=agg.sources_independent,
        business_conflict=path_business_conflict,
        business_object=business_object,
        purpose=purpose,
        candidate_status="available",
        original_standard_item_id="" if exact_item is None else exact_item.item_id,
    )


def classify_all(
    components: Sequence[CashflowComponent],
    rules: RulePack,
    dictionary: object | None = None,
    summary_dictionary: object | None = None,
) -> tuple[ClassificationDecision, ...]:
    return tuple(
        classify_component(component, rules, dictionary, summary_dictionary)
        for component in components
    )


def _is_unknown_service_individual_tax(component: CashflowComponent) -> bool:
    text = "|".join(
        (component.summary, *component.counterpart_accounts)
    )
    if not ("个税" in text or "个人所得税" in text):
        return False
    return not any(
        term in text
        for term in (
            "工资",
            "薪酬",
            "职工",
            "员工",
            "奖金",
            "分红",
            "股利",
            "劳务报酬",
        )
    )


def _materiality_grouping_basis(
    component: CashflowComponent,
    decision: ClassificationDecision,
    level1_accounts: Sequence[str],
) -> tuple[bool, str, str]:
    """判断是否具备会影响动作的可靠同类依据，并返回稳定用途。"""
    if not decision.system_item_id or decision.candidate_status != "available":
        return False, "候选项目尚未唯一，只作潜在累计风险提示", ""
    if not level1_accounts or any(not value.strip() for value in level1_accounts):
        return False, "标准一级科目不完整，只作潜在累计风险提示", ""
    semantic_purposes = {
        value.strip()
        for value in decision.purpose.split("、")
        if value.strip()
    }
    if len(semantic_purposes) > 1:
        return False, "存在多个不同明细用途，只作潜在累计风险提示", ""
    detail_purposes = semantic_purposes or {
        levels[-1]
        for account in component.counterpart_accounts
        if len(levels := split_account_levels(account)) > 1
        and levels[-1].strip()
    }
    if len(detail_purposes) != 1:
        reason = "明细用途缺失" if not detail_purposes else "存在多个不同明细用途"
        return False, reason + "，只作潜在累计风险提示", ""
    purpose = next(iter(detail_purposes))
    broad_purposes = {
        "其他",
        "往来",
        "往来款",
        "其他应收款",
        "其他应付款",
        "其他收入",
        "其他支出",
        "其他费用",
        "未分类",
        "待判断",
    }
    broad_by_wording = bool(
        "往来" in purpose
        or "进项税" in purpose
        or "销项税" in purpose
        or (
            purpose.startswith("其他")
            and any(
                term in purpose
                for term in ("收入", "收益", "支出", "费用", "应收", "应付")
            )
        )
    )
    if purpose in broad_purposes or broad_by_wording:
        return False, f"明细用途“{purpose}”过宽，只作潜在累计风险提示", purpose
    return True, "唯一候选、标准一级科目和单一明细用途均明确", purpose


def route_classification_decisions(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    materiality,
    company_notes: Sequence[dict[str, object]] = (),
) -> ClassificationRoutingResult:
    from cashflow_direct.ai_review import (
        build_ai_task,
        company_note_applies,
        company_note_is_active,
    )

    if any(item.account_mapping_status != "confirmed" for item in components):
        raise ValueError("一级科目映射未全部确认，不能进入现金流分类")
    if {item.component_id for item in components} != {
        item.component_id for item in decisions
    }:
        raise ValueError("现金流业务组成与候选判断必须逐笔对应")
    conflict_component_ids = {
        component_id
        for group in find_consistency_groups(components, decisions, materiality)
        for component_id in group.component_ids
    }
    decisions = tuple(
        replace(item, business_conflict=True)
        if item.component_id in conflict_component_ids
        else item
        for item in decisions
    )
    decision_by_id = {item.component_id: item for item in decisions}
    rule_pack = load_rule_pack(Path(__file__).resolve().parents[2])
    records = []
    for component in components:
        decision = decision_by_id[component.component_id]
        level1_accounts = tuple(
            sorted(
                {
                    levels[0]
                    for account in component.counterpart_accounts
                    if (levels := split_account_levels(account))
                }
            )
        )
        grouping_reliable, grouping_reason, stable_purpose = (
            _materiality_grouping_basis(component, decision, level1_accounts)
        )
        records.append(
            MaterialityRecord(
                record_id=component.component_id,
                amount_cent=component.cash_delta_cent,
                cash_direction=(
                    "inflow" if component.cash_delta_cent > 0 else "outflow"
                ),
                candidate_item_id=(
                    "" if decision.source_conflict else decision.system_item_id
                ),
                standard_level1_account="、".join(level1_accounts),
                business_object=decision.business_object,
                purpose=stable_purpose,
                grouping_reliable=grouping_reliable,
                grouping_reason=grouping_reason,
            )
        )
    assessments = assess_materiality_records(tuple(records), materiality)
    assessment_by_id = {item.record_id: item for item in assessments}
    routed: list[ClassificationDecision] = []
    tasks = []
    automatic_actions = {
        DecisionAction.AUTOMATIC_KEEP,
        DecisionAction.AUTOMATIC_FILL,
        DecisionAction.AUTOMATIC_CHANGE,
    }
    for component in components:
        decision = decision_by_id[component.component_id]
        if decision.excluded:
            routed.append(decision)
            continue
        assessment = assessment_by_id[component.component_id]
        original_state = OriginalItemState(decision.original_item_state)
        actual_direction = "inflow" if component.cash_delta_cent > 0 else "outflow"
        approved_reversal_rule_ids = tuple(
            rule.rule_id
            for rule in rule_pack.rules
            if rule.item_id == decision.system_item_id
            and _rule_matches(rule, component)
            and rule_pack.item_by_id[rule.item_id].normal_direction != actual_direction
        )
        approved_reversal_rule_ids += tuple(
            str(note.get("note_id", ""))
            for note in company_notes
            if company_note_is_active(note)
            and str(note.get("规则类型", "")) == "退款或反向冲减"
            and company_note_applies(
                note, component.summary, component.counterpart_accounts
            )
        )
        if approved_reversal_rule_ids:
            direction_status = "approved_reversal"
        elif decision.system_item_id and decision.normal_direction != actual_direction:
            direction_status = "incompatible"
        else:
            direction_status = "compatible"
        new_reversal_pattern = bool(
            direction_status == "incompatible"
            and any(
                term in f"{component.summary}|{decision.purpose}"
                for term in (
                    "退款",
                    "退回",
                    "退还",
                    "退货",
                    "返还",
                    "冲减",
                    "冲回",
                    "红字",
                    "撤销",
                    "退付",
                )
            )
        )
        reversal_rejected = any(
            str(note.get("规则类型", "")) == "退款或反向冲减"
            and str(note.get("状态", "")) == "冲突未采用"
            and company_note_applies(
                {**note, "状态": "采用"},
                component.summary,
                component.counterpart_accounts,
            )
            for note in company_notes
        )
        new_reversal_pattern = new_reversal_pattern and not reversal_rejected
        invalid_input = bool(
            {"summary_empty", "account_path_empty", "account_path_invalid"}
            .intersection(component.anomalies)
        )
        individual_tax_fact_missing = _is_unknown_service_individual_tax(component)
        has_vat_path = any(
            any(term in account for term in ("进项税", "销项税"))
            for account in component.counterpart_accounts
        )
        has_non_vat_path = any(
            not any(term in account for term in ("进项税", "销项税"))
            for account in component.counterpart_accounts
        )
        vat_base_missing = has_vat_path and not has_non_vat_path
        applicable_company_notes = tuple(
            note
            for note in company_notes
            if company_note_applies(
                note, component.summary, component.counterpart_accounts
            )
        )
        company_rule_outcomes = {
            str(note.get("建议处理", "")).strip()
            or str(note.get("内容", "")).strip()
            for note in applicable_company_notes
        }
        company_rule_conflict = len(company_rule_outcomes) > 1
        net_item_facts_missing = bool(
            decision.system_item_id in {"CFI-03", "CFI-04", "CFI-08"}
            and not any(
                company_note_is_active(note)
                and str(note.get("规则类型", "")) == "净额项目资料确认"
                and company_note_applies(
                    note, component.summary, component.counterpart_accounts
                )
                for note in company_notes
            )
        )
        business_conflict = bool(
            getattr(decision, "business_conflict", False)
        )
        ordinary_group = not (
            invalid_input
            or company_rule_conflict
            or vat_base_missing
            or net_item_facts_missing
            or individual_tax_fact_missing
            or decision.source_conflict
            or business_conflict
            or direction_status == "incompatible"
        )
        needs_group_confirmation = (
            ordinary_group
            and assessment.grouping_status == "reliable"
            and assessment.single_level is not MaterialityLevel.M3
            and assessment.cumulative_level is MaterialityLevel.M3
        )
        routing_level = (
            assessment.single_level
            if needs_group_confirmation
            else assessment.effective_level
        )
        route = route_decision(
            score=decision.evidence_score,
            original_state=original_state,
            materiality=MaterialityLevel(routing_level.value),
            invalid_input=invalid_input,
            company_rule_conflict=company_rule_conflict,
            vat_base_missing=vat_base_missing,
            net_item_facts_missing=net_item_facts_missing,
            individual_tax_fact_missing=individual_tax_fact_missing,
            new_reversal_pattern=new_reversal_pattern,
            source_conflict=decision.source_conflict,
            business_conflict=business_conflict,
            direction_status=direction_status,
        )
        is_automatic = route.action in automatic_actions
        if company_rule_conflict:
            route_reason = (
                f"{decision.reason}；同一业务同时命中相互冲突的公司规则，必须人工决定"
            )
        elif vat_base_missing:
            route_reason = (
                f"{decision.reason}；进项税或销项税缺少同一现金业务内已识别的基础交易，不能据此修改原项目"
            )
        elif individual_tax_fact_missing:
            route_reason = f"{decision.reason}；个税服务对象不明，不能据此自动修改原项目"
        elif net_item_facts_missing:
            route_reason = (
                f"{decision.reason}；净额资料尚未确认完整，"
                "不能据此自动修改原项目"
            )
        elif (
            assessment.grouping_status == "potential"
            and assessment.cumulative_level != assessment.single_level
        ):
            route_reason = (
                f"{decision.reason}；{assessment.grouping_reason}；"
                f"潜在累计金额{assessment.same_class_total_cent / 100:.2f}元不参与升层"
            )
        else:
            route_reason = decision.reason
        result_item_id = decision.system_item_id
        result_item_name = decision.system_item_name
        result_direction = decision.normal_direction
        if (
            route.action is DecisionAction.AUTOMATIC_KEEP
            and decision.original_standard_item_id
        ):
            original_item = rule_pack.item_by_id[decision.original_standard_item_id]
            result_item_id = original_item.item_id
            result_item_name = original_item.name
            result_direction = original_item.normal_direction
            if decision.system_item_id != result_item_id:
                route_reason += "；修改证据未达到门槛，保留原项目"
        current = replace(
            decision,
            system_item_id=result_item_id,
            system_item_name=result_item_name,
            normal_direction=result_direction,
            resolved=is_automatic or decision.excluded,
            decision_source=(
                "system_automatic" if is_automatic else decision.decision_source
            ),
            decision_action=route.action.value,
            ai_review_policy=route.review_policy,
            materiality_level=routing_level.value,
            single_materiality_level=assessment.single_level.value,
            cumulative_materiality_level=assessment.cumulative_level.value,
            materiality_group_id=assessment.group_id,
            materiality_group_confirmation_status=(
                "pending_in_final_workbook"
                if needs_group_confirmation
                else "not_required"
            ),
            materiality_grouping_status=assessment.grouping_status,
            materiality_grouping_reason=assessment.grouping_reason,
            direction_status=direction_status,
            business_conflict=business_conflict,
            company_rule_conflict=company_rule_conflict,
            vat_base_missing=vat_base_missing,
            net_item_facts_missing=net_item_facts_missing,
            individual_tax_fact_missing=individual_tax_fact_missing,
            new_reversal_pattern=new_reversal_pattern,
            approved_reversal_rule_ids=approved_reversal_rule_ids,
            reason=route_reason,
        )
        routed.append(current)
        if route.action is DecisionAction.AI_REVIEW:
            task_decision = current
            if route.forced_check == "direction" or not any(
                current.candidate_item_ids
            ):
                compatible_ids = tuple(
                    item.item_id
                    for item in load_rule_pack(
                        Path(__file__).resolve().parents[2]
                    ).statement_items
                    if item.is_leaf and item.normal_direction == actual_direction
                )
                task_decision = replace(
                    current,
                    candidate_item_ids=tuple(
                        dict.fromkeys(
                            (*filter(None, current.candidate_item_ids), *compatible_ids)
                        )
                    ),
                    summary_candidate_item_ids=(
                        current.summary_candidate_item_ids
                        if current.summary_candidate_item_ids
                        else compatible_ids
                    ),
                    account_path_candidate_item_ids=(
                        current.account_path_candidate_item_ids
                        if current.account_path_candidate_item_ids
                        else compatible_ids
                    ),
                )
            tasks.append(build_ai_task(component, task_decision, company_notes))
        elif route.action is DecisionAction.DOUBLE_AI_REVIEW:
            task_decision = current
            if route.forced_check == "direction" or not any(
                current.candidate_item_ids
            ):
                compatible_ids = tuple(
                    item.item_id
                    for item in load_rule_pack(
                        Path(__file__).resolve().parents[2]
                    ).statement_items
                    if item.is_leaf and item.normal_direction == actual_direction
                )
                task_decision = replace(
                    current,
                    candidate_item_ids=tuple(
                        dict.fromkeys(
                            (*filter(None, current.candidate_item_ids), *compatible_ids)
                        )
                    ),
                    summary_candidate_item_ids=(
                        current.summary_candidate_item_ids
                        if current.summary_candidate_item_ids
                        else compatible_ids
                    ),
                    account_path_candidate_item_ids=(
                        current.account_path_candidate_item_ids
                        if current.account_path_candidate_item_ids
                        else compatible_ids
                    ),
                )
            base = build_ai_task(component, task_decision, company_notes)
            for slot in ("A", "B"):
                tasks.append(
                    replace(
                        base,
                        task_id=stable_id(
                            "AI",
                            component.component_id,
                            decision.system_item_id,
                            "blind",
                            slot,
                        ),
                        context=f"{base.context}；独立复核{slot}：不得查看另一复核结果",
                    )
                )
    return ClassificationRoutingResult(tuple(routed), tuple(tasks), assessments)
