from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from cashflow_direct.account_dictionary import score_dictionary_hits
from cashflow_direct.evidence import (
    SOURCE_ACCOUNT_PATH,
    SOURCE_SUMMARY,
    RuleScore,
    aggregate_evidence,
)
from cashflow_direct.decision_policy import (
    DEFAULT_AUTOMATIC_CHANGE_SCORE,
    DecisionAction,
    MaterialityLevel,
    OriginalItemState,
    route_decision,
)
from cashflow_direct.consistency import find_consistency_groups
from cashflow_direct.components import ComponentSourceAllocation
from cashflow_direct.materiality import (
    MaterialityAssessment,
    MaterialityRecord,
    assess_materiality_records,
)
from cashflow_direct.models import CashflowComponent, ClassificationDecision
from cashflow_direct.money import stable_id
from cashflow_direct.rule_registry import default_rule_registry, load_rule_registry
from cashflow_direct.summary_semantics import SummarySemanticResult
from cashflow_direct.vat_companion import (
    apply_vat_companion_relations,
    build_vat_companion_relations,
)

# 来源中文名映射（用于 reason 展示，最终 xlsx 人类可读列一律中文）
_SOURCE_CN = {
    SOURCE_SUMMARY: "摘要",
    SOURCE_ACCOUNT_PATH: "完整对方科目路径",
}
_AUTOMATIC_ACTIONS = frozenset(
    DecisionAction(value)
    for value in default_rule_registry().action_group("automatic")
)


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
class RulePack:
    statement_items: tuple[StatementItem, ...]

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
    item_payload = load_rule_registry(Path(root)).statement_policy
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
    if len(items) != 35 or len({item.item_id for item in items}) != 35:
        raise ValueError("一般企业正表项目必须恰好包含 35 个唯一行项目")
    return RulePack(items)


def _score_summary_semantics(
    component: CashflowComponent,
    result: SummarySemanticResult,
    rules: RulePack,
) -> RuleScore | None:
    if result.summary != component.summary:
        raise ValueError("摘要语义结果与当前业务摘要不一致")
    if result.status == "needs_agent":
        raise RuntimeError("摘要语义尚未完成，不能进入现金流分类")
    if result.quality.value == 0 or not result.candidate_item_ids:
        return None
    unknown = set(result.candidate_item_ids).difference(rules.item_by_id)
    if unknown:
        raise ValueError(f"摘要语义引用了不存在的正表项目：{'、'.join(sorted(unknown))}")
    item_id = result.candidate_item_ids[0] if len(result.candidate_item_ids) == 1 else ""
    semantic_spans = tuple(
        span
        for span in result.spans
        if span.slot not in {"noise", "noise_date", "noise_amount", "noise_number"}
    )
    return RuleScore(
        rule_id=f"SUMMARY-SEMANTICS-{component.component_id}",
        item_id=item_id,
        priority=-100,
        source=SOURCE_SUMMARY,
        score=result.quality.value,
        summary_part=result.quality.value,
        account_part=0,
        direction_compatible=(
            True
            if not item_id
            else rules.item_by_id[item_id].normal_direction
            == ("inflow" if component.cash_delta_cent > 0 else "outflow")
        ),
        summary_hits=tuple(span.text for span in semantic_spans),
        account_hits=(),
        channels=(SOURCE_SUMMARY,),
        summary_facts=tuple(f"{span.slot}:{span.text}" for span in semantic_spans),
        business_object="、".join(
            dict.fromkeys(span.text for span in result.spans if span.slot == "business_object")
        ),
        purpose="、".join(
            dict.fromkeys(
                span.text for span in result.spans if span.slot in {"purpose", "attribute"}
            )
        ),
        candidate_item_ids=result.candidate_item_ids,
    )


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
    summary_semantics: Mapping[str, SummarySemanticResult] | None = None,
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
    if not component.summary.strip() or illegal_markers.intersection(component.anomalies):
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
        exclusion_type = (
            "zero_amount"
            if component.cash_delta_cent == 0
            else "internal_transfer"
            if "internal_transfer" in component.anomalies
            else "non_cash"
        )
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
            exclusion_type=exclusion_type,
        )

    evidence_component = component
    if summary_semantics is None or component.summary not in summary_semantics:
        raise RuntimeError("摘要语义尚未完成，不能进入现金流分类")
    summary_result = summary_semantics[component.summary]
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
    summary_score = _score_summary_semantics(evidence_component, summary_result, rules)
    if not dictionary_scores and summary_score is None:
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
            summary_candidate_item_ids=summary_result.candidate_item_ids,
            original_item_state=pending_original_state,
            summary_quality=summary_result.quality.value,
            candidate_status="no_candidate",
            original_standard_item_id="" if exact_item is None else exact_item.item_id,
        )

    rule_scores = list(dictionary_scores)
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
    if best_score.summary_part:
        reason = (
            f"摘要固定语义形成“{item.name}”候选："
            f"{'、'.join(best_score.summary_hits)}"
        )
        path_hits = tuple(
            hit
            for score in agg.rule_scores
            if score.account_part
            and (score.item_id == agg.item_id or agg.item_id in score.candidate_item_ids)
            for hit in score.account_hits
        )
        if path_hits:
            reason += f"；完整对方科目路径同时支持：{'、'.join(path_hits)}"
    else:
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
    summary_semantics: Mapping[str, SummarySemanticResult] | None = None,
) -> tuple[ClassificationDecision, ...]:
    return tuple(
        classify_component(component, rules, dictionary, summary_semantics)
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


def route_classification_decisions(
    components: Sequence[CashflowComponent],
    decisions: Sequence[ClassificationDecision],
    materiality,
    company_notes: Sequence[dict[str, object]] = (),
    automatic_change_threshold: int = DEFAULT_AUTOMATIC_CHANGE_SCORE,
    source_allocations: Sequence[ComponentSourceAllocation] = (),
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
    records = [
        MaterialityRecord(component.component_id, component.cash_delta_cent)
        for component in components
    ]
    assessments = assess_materiality_records(tuple(records), materiality)
    assessment_by_id = {item.record_id: item for item in assessments}
    routed: list[ClassificationDecision] = []
    tasks = []
    automatic_actions = _AUTOMATIC_ACTIONS
    for component in components:
        decision = decision_by_id[component.component_id]
        if decision.excluded:
            routed.append(decision)
            continue
        assessment = assessment_by_id[component.component_id]
        original_state = OriginalItemState(decision.original_item_state)
        actual_direction = "inflow" if component.cash_delta_cent > 0 else "outflow"
        if decision.system_item_id and decision.normal_direction != actual_direction:
            direction_status = "incompatible"
        else:
            direction_status = "compatible"
        invalid_input = bool(
            not component.summary.strip()
            or decision.candidate_status == "invalid_input"
            or {"summary_empty", "account_path_empty", "account_path_invalid"}
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
        invalid_company_rule = any(
            not company_note_applies(
                note, component.summary, component.counterpart_accounts
            )
            and company_note_applies(
                {
                    **note,
                    "状态": "采用",
                    "适用完整路径": (),
                    "适用标准一级科目": (),
                    "适用中间层级": (),
                    "适用末级明细": (),
                },
                component.summary,
                component.counterpart_accounts,
            )
            for note in company_notes
        )
        company_rule_conflict = len(company_rule_outcomes) > 1 or invalid_company_rule
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
        routing_level = assessment.single_level
        route = route_decision(
            score=decision.evidence_score,
            original_state=original_state,
            materiality=MaterialityLevel(routing_level.value),
            invalid_input=invalid_input,
            company_rule_conflict=company_rule_conflict,
            vat_base_missing=vat_base_missing,
            net_item_facts_missing=net_item_facts_missing,
            individual_tax_fact_missing=individual_tax_fact_missing,
            source_conflict=decision.source_conflict,
            business_conflict=business_conflict,
            direction_status=direction_status,
            automatic_change_threshold=automatic_change_threshold,
            summary_quality=decision.summary_quality,
            account_path_quality=decision.account_path_quality,
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
            decision_rule_id=route.rule_id,
            ai_review_policy=route.review_policy,
            materiality_level=routing_level.value,
            single_materiality_level=assessment.single_level.value,
            direction_status=direction_status,
            business_conflict=business_conflict,
            company_rule_conflict=company_rule_conflict,
            vat_base_missing=vat_base_missing,
            net_item_facts_missing=net_item_facts_missing,
            individual_tax_fact_missing=individual_tax_fact_missing,
            reason=route_reason,
        )
        routed.append(current)
        if route.action is DecisionAction.AI_REVIEW:
            task_decision = current
            if not any(current.candidate_item_ids):
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
            if not any(current.candidate_item_ids):
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
    relations = build_vat_companion_relations(components, source_allocations)
    routed = list(apply_vat_companion_relations(routed, relations))
    dependent_vat_ids = {
        relation.vat_component_id
        for relation in relations
        if relation.status == "unique"
    }
    tasks = [
        task for task in tasks if task.component_id not in dependent_vat_ids
    ]
    return ClassificationRoutingResult(tuple(routed), tuple(tasks), assessments)
