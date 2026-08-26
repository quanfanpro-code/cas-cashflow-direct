from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path

from cashflow_direct.decision_policy import (
    DEFAULT_AUTOMATIC_CHANGE_SCORE,
    DecisionAction,
    EvidenceQuality,
    EvidenceSource,
    EvidenceSourceAssessment,
    MaterialityLevel,
    OriginalItemState,
    combine_source_assessments,
    score_meets_change_threshold,
    unresolved_after_ai_review_action,
    validate_automatic_change_threshold,
)
from cashflow_direct.evidence import split_account_levels
from cashflow_direct.models import (
    AITask,
    CashflowComponent,
    ClassificationDecision,
)
from cashflow_direct.money import stable_id
from cashflow_direct.rule_registry import default_rule_registry


_AI_RULES = default_rule_registry()
ACTIVE_COMPANY_NOTE_STATUSES = frozenset(
    _AI_RULES.special_policy["company_rule_inputs"]["active_statuses"]
)
_AI_OUTCOME_POLICY = _AI_RULES.evidence_policy["ai_review_outcomes"]
_AI_UNRESOLVED_ACTION = unresolved_after_ai_review_action()
_BLANK_SINGLE_MINIMUM_SCORES = dict(
    _AI_OUTCOME_POLICY["blank_single_minimum_scores"]
)


def company_note_is_active(note: Mapping[str, object]) -> bool:
    return str(note.get("状态", "采用")) in ACTIVE_COMPANY_NOTE_STATUSES


def _note_values(note: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = note.get(key, ())
    values = raw if isinstance(raw, (list, tuple)) else (raw,)
    return tuple(str(value).strip() for value in values if str(value).strip())


def company_note_applies(
    note: Mapping[str, object],
    summary: str,
    account_paths: Sequence[str],
) -> bool:
    if not company_note_is_active(note):
        return False
    levels = tuple(split_account_levels(path) for path in account_paths if path.strip())
    checks = (
        ("适用完整路径", set(account_paths)),
        ("适用标准一级科目", {item[0] for item in levels if item}),
        ("适用中间层级", {part for item in levels for part in item[1:-1]}),
        ("适用末级明细", {item[-1] for item in levels if item}),
    )
    scope_configured = False
    for key, actual in checks:
        required = _note_values(note, key)
        scope_configured = scope_configured or bool(required)
        if required and not set(required).intersection(actual):
            return False
    summary_terms = _note_values(note, "适用摘要词")
    scope_configured = scope_configured or bool(summary_terms)
    if summary_terms and not any(term in summary for term in summary_terms):
        return False
    terms = _note_values(note, "涉及科目或词")
    if not terms:
        return scope_configured
    return any(
        term in summary or any(term in path for path in account_paths)
        for term in terms
    )


@dataclass(frozen=True, slots=True)
class AISourceReview:
    candidate_item_id: str
    quality: EvidenceQuality
    basis_text: str
    classification_facts: tuple[str, ...]
    conflict: bool
    candidate_item_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        candidates = tuple(
            dict.fromkeys(
                self.candidate_item_ids
                or ((self.candidate_item_id,) if self.candidate_item_id else ())
            )
        )
        object.__setattr__(self, "candidate_item_ids", candidates)


@dataclass(frozen=True, slots=True)
class StructuredAIResult:
    task_id: str
    component_id: str
    summary: AISourceReview
    account_path: AISourceReview
    sources_independent: bool
    business_conflict: bool
    direction_status: str
    reason: str
    alternative_item_ids: tuple[str, ...]
    note_ids: tuple[str, ...]
    review_round: str = ""
    reviewer_id: str = ""
    model_id: str = ""
    reviewed_at: str = ""
    prior_result_difference: str = ""


@dataclass(frozen=True, slots=True)
class StructuredAIValidation:
    valid_results: tuple[StructuredAIResult, ...]
    missing_ids: tuple[str, ...]
    duplicate_ids: tuple[str, ...]
    invalid_ids: tuple[str, ...]
    status: str


def structured_ai_result_from_mapping(
    payload: Mapping[str, object],
) -> StructuredAIResult:
    def source(key: str) -> AISourceReview:
        item = payload[key]
        if not isinstance(item, Mapping):
            raise ValueError(f"AI结构化结果缺少来源：{key}")
        raw_quality = item.get("quality")
        quality = (
            _AI_QUALITY_NAMES.get(str(raw_quality))
            if isinstance(raw_quality, str)
            else EvidenceQuality(int(raw_quality))
        )
        if quality is None:
            raise ValueError(f"AI结构化结果质量非法：{raw_quality}")
        return AISourceReview(
            candidate_item_id=str(item.get("candidate_item_id", "")),
            quality=quality,
            basis_text=str(item.get("basis_text", "")),
            classification_facts=tuple(
                str(value) for value in item.get("classification_facts", ())
            ),
            conflict=bool(item.get("conflict", False)),
            candidate_item_ids=tuple(
                str(value) for value in item.get("candidate_item_ids", ())
            ),
        )

    return StructuredAIResult(
        task_id=str(payload["task_id"]),
        component_id=str(payload["component_id"]),
        summary=source("summary"),
        account_path=source("account_path"),
        sources_independent=bool(payload.get("sources_independent", False)),
        business_conflict=bool(payload.get("business_conflict", False)),
        direction_status=str(payload.get("direction_status", "")),
        reason=str(payload.get("reason", "")),
        alternative_item_ids=tuple(
            str(value) for value in payload.get("alternative_item_ids", ())
        ),
        note_ids=tuple(str(value) for value in payload.get("note_ids", ())),
        review_round=str(payload.get("review_round", "")),
        reviewer_id=str(payload.get("reviewer_id", "")),
        model_id=str(payload.get("model_id", "")),
        reviewed_at=str(payload.get("reviewed_at", "")),
        prior_result_difference=str(payload.get("prior_result_difference", "")),
    )


def redact_text(text: str) -> str:
    """在 AI 请求生成前遮蔽身份证、长账号和手机号。"""
    masked = re.sub(r"(?<!\d)\d{6}(?:19|20)\d{2}\d{2}\d{2}\d{3}[\dXx](?!\d)", "[身份证已遮蔽]", text)
    masked = re.sub(r"(?<!\d)\d{12,19}(?!\d)", "[账号已遮蔽]", masked)
    return re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已遮蔽]", masked)


def build_ai_task(
    component: CashflowComponent,
    decision: ClassificationDecision,
    company_notes: Sequence[Mapping[str, object]] = (),
) -> AITask:
    candidates = tuple(
        dict.fromkeys(decision.candidate_item_ids or (decision.system_item_id,))
    )
    context = redact_text(
        f"摘要原文：{component.summary}；"
        f"完整对方科目路径：{'、'.join(component.counterpart_accounts)}"
    )
    return AITask(
        task_id=stable_id("AI", component.component_id, decision.system_item_id),
        component_id=component.component_id,
        context=context,
        original_item="",
        system_item_id=decision.system_item_id,
        rule_evidence="系统候选仅供复核，不得作为新增事实",
        candidate_item_ids=candidates,
        summary_candidate_item_ids=(
            candidates
            if decision.summary_candidate_item_ids is None
            else decision.summary_candidate_item_ids
        ),
        account_path_candidate_item_ids=(
            candidates
            if decision.account_path_candidate_item_ids is None
            else decision.account_path_candidate_item_ids
        ),
    )


def build_blind_ai_tasks(
    component: CashflowComponent,
    decision: ClassificationDecision,
    slots: Sequence[str],
    company_notes: Sequence[Mapping[str, object]] = (),
) -> tuple[AITask, ...]:
    """只用原始摘要和完整路径生成互盲任务，不传递其他AI的意见。"""
    allowed_slots = {"A", "B", "C"}
    if not slots or any(slot not in allowed_slots for slot in slots):
        raise ValueError("互盲复核轮次只允许A、B或C")
    base = build_ai_task(component, decision, company_notes)
    tasks = []
    for slot in slots:
        prohibition = (
            "不得查看独立复核A、B结果"
            if slot == "C"
            else "不得查看另一复核结果"
        )
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
                context=f"{base.context}；独立复核{slot}：{prohibition}",
            )
        )
    return tuple(tasks)


def review_text_pattern(text: str) -> str:
    """复核分组用的文本模式：剔除日期、数字与标点，仅保留业务文字。"""
    without_dates = re.sub(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?", "", text)
    without_numbers = re.sub(r"\d[\d,，.]*", "", without_dates)
    normalized = re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】_-]+", "", without_numbers)
    return normalized.lower() or "空白"


def chunk_ai_tasks(
    tasks: Sequence[AITask],
    size: int = 25,
) -> tuple[tuple[AITask, ...], ...]:
    if not 1 <= size <= 25:
        raise ValueError("AI 单批数量必须在 1 至 25 之间")
    frozen = tuple(tasks)
    return tuple(frozen[index : index + size] for index in range(0, len(frozen), size))


def validate_basis_text(text: str) -> str | None:
    """AI 依据门禁：理由必须含四类可追查依据之一，否则返回拒绝原因。

    四类依据：准则条款（含"准则"且含"第×条"或"第×项"）、应用指南（含"应用指南"且含章节号
    或不短于 4 字的引文）、知识库（含"知识库"且含行号/章节/引文）、公司特殊规则
    NOTE 编号。只在导入校验环节把关，防止空泛理由进入复核链。
    """
    stripped = (text or "").strip()
    if not stripped:
        return "理由为空"
    if re.search(r"NOTE-\d+", stripped):
        return None
    clause = re.search(r"第[0-9一二三四五六七八九十百]+[条项]", stripped)
    if "准则" in stripped and clause:
        return None
    quoted = re.search(r"[\"'“”‘’《》][^\"'“”‘’《》]{4,}[\"'“”‘’《》]", stripped)
    chapter = re.search(r"第[0-9一二三四五六七八九十百]+[章节]", stripped)
    if "应用指南" in stripped and (chapter or quoted):
        return None
    if "知识库" in stripped and (re.search(r"第?\d+行", stripped) or chapter or quoted):
        return None
    return "理由缺少可追查依据（须含准则条款、应用指南章节或引文、知识库位置或 NOTE-编号）"


_AI_QUALITY_NAMES = {
    "invalid": EvidenceQuality.INVALID,
    "weak": EvidenceQuality.WEAK,
    "medium": EvidenceQuality.MEDIUM,
    "strong": EvidenceQuality.STRONG,
}


def _quality_from_payload(value: object) -> EvidenceQuality | None:
    if isinstance(value, str):
        return _AI_QUALITY_NAMES.get(value)
    try:
        return EvidenceQuality(int(value))
    except (TypeError, ValueError):
        return None
_AI_FORBIDDEN_FIELDS = {
    "score",
    "total",
    "amount",
    "amount_cent",
    "action",
    "permission",
    "materiality",
}


def _second_review_difference_is_substantive(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text or "")
    boilerplate = (
        "与上一轮一致",
        "同意上一轮",
        "无差异",
        "没有差异",
        "重复第一次",
        "未查看另一结果",
    )
    if not normalized or any(term in normalized for term in boilerplate):
        return False
    return any(
        term in normalized
        for term in ("原文", "结构", "规则", "依据", "质量", "候选", "冲突", "语义")
    )


def _parse_source_review(
    payload: object,
    task: AITask,
    valid_item_ids: set[str],
    source: EvidenceSource,
) -> AISourceReview | None:
    if not isinstance(payload, Mapping) or _AI_FORBIDDEN_FIELDS.intersection(payload):
        return None
    required = {
        "quality",
        "basis_text",
        "classification_facts",
        "conflict",
    }
    if not required.issubset(payload) or not {
        "candidate_item_id", "candidate_item_ids"
    }.intersection(payload):
        return None
    candidate = payload.get("candidate_item_id")
    raw_candidates = payload.get("candidate_item_ids", ())
    quality = _quality_from_payload(payload.get("quality"))
    basis = payload.get("basis_text")
    facts = payload.get("classification_facts")
    conflict = payload.get("conflict")
    if (
        not isinstance(candidate, str)
        or not isinstance(raw_candidates, (list, tuple))
        or not all(isinstance(value, str) for value in raw_candidates)
        or quality is None
        or not isinstance(basis, str)
        or not isinstance(facts, list)
        or not all(isinstance(fact, str) and fact.strip() for fact in facts)
        or not isinstance(conflict, bool)
    ):
        return None
    candidates = tuple(
        dict.fromkeys(
            tuple(str(value) for value in raw_candidates)
            or ((candidate,) if candidate else ())
        )
    )
    source_candidates = (
        task.summary_candidate_item_ids
        if source is EvidenceSource.SUMMARY
        else task.account_path_candidate_item_ids
    )
    allowed_candidates = set(
        (task.candidate_item_ids or (task.system_item_id,))
        if source_candidates is None
        else source_candidates
    )
    if quality is EvidenceQuality.INVALID:
        if candidates or basis or facts:
            return None
    elif (
        not candidates
        or any(value not in valid_item_ids for value in candidates)
        or not set(candidates).issubset(allowed_candidates)
        or (len(candidates) > 1 and quality is not EvidenceQuality.WEAK)
        or not basis.strip()
        or basis.strip() not in task.context
        or not facts
    ):
        return None
    return AISourceReview(
        candidate,
        quality,
        basis.strip(),
        tuple(str(fact).strip() for fact in facts),
        conflict,
        candidates,
    )


def validate_structured_ai_results(
    expected: Sequence[AITask],
    payloads: Sequence[Mapping[str, object]],
    valid_item_ids: set[str],
) -> StructuredAIValidation:
    expected_by_id = {task.task_id: task for task in expected}
    counts = Counter(str(payload.get("task_id", "")) for payload in payloads)
    duplicate_ids = tuple(
        sorted(task_id for task_id, count in counts.items() if task_id and count > 1)
    )
    valid: list[StructuredAIResult] = []
    invalid: set[str] = set()
    required = {
        "task_id",
        "component_id",
        "summary",
        "account_path",
        "sources_independent",
        "business_conflict",
        "direction_status",
        "reason",
        "alternative_item_ids",
        "note_ids",
        "review_round",
        "reviewer_id",
        "model_id",
        "reviewed_at",
        "prior_result_difference",
    }
    for payload in payloads:
        task_id = str(payload.get("task_id", ""))
        task = expected_by_id.get(task_id)
        forbidden = _AI_FORBIDDEN_FIELDS.intersection(payload)
        if task is None or counts[task_id] != 1 or forbidden or not required.issubset(payload):
            if task_id:
                invalid.add(task_id)
            continue
        summary = _parse_source_review(
            payload.get("summary"), task, valid_item_ids, EvidenceSource.SUMMARY
        )
        account = _parse_source_review(
            payload.get("account_path"),
            task,
            valid_item_ids,
            EvidenceSource.ACCOUNT_PATH,
        )
        component_id = payload.get("component_id")
        independent = payload.get("sources_independent")
        business_conflict = payload.get("business_conflict")
        direction_status = payload.get("direction_status")
        reason = payload.get("reason")
        alternatives = payload.get("alternative_item_ids")
        note_ids = payload.get("note_ids")
        review_round = payload.get("review_round")
        reviewer_id = payload.get("reviewer_id")
        model_id = payload.get("model_id")
        reviewed_at = payload.get("reviewed_at")
        prior_difference = payload.get("prior_result_difference")
        expected_round = (
            "A"
            if "；独立复核A：" in task.context
            else "B"
            if "；独立复核B：" in task.context
            else "C"
            if "；独立复核C：" in task.context
            else "single"
        )
        try:
            reviewed_time = datetime.fromisoformat(
                str(reviewed_at).replace("Z", "+00:00")
            )
            valid_reviewed_at = reviewed_time.tzinfo is not None
        except ValueError:
            valid_reviewed_at = False
        is_valid = (
            summary is not None
            and account is not None
            and component_id == task.component_id
            and isinstance(independent, bool)
            and isinstance(business_conflict, bool)
            and direction_status in {"compatible", "incompatible"}
            and isinstance(reason, str)
            and bool(reason.strip())
            and isinstance(alternatives, list)
            and all(
                item in valid_item_ids
                and item in set(task.candidate_item_ids or (task.system_item_id,))
                for item in alternatives
            )
            and isinstance(note_ids, list)
            and all(
                isinstance(note_id, str)
                and re.fullmatch(r"NOTE-\d+", note_id)
                and note_id in task.context
                for note_id in note_ids
            )
            and review_round == expected_round
            and isinstance(reviewer_id, str)
            and bool(reviewer_id.strip())
            and isinstance(model_id, str)
            and bool(model_id.strip())
            and isinstance(reviewed_at, str)
            and valid_reviewed_at
            and isinstance(prior_difference, str)
            and bool(prior_difference.strip())
            and (
                expected_round != "second"
                or _second_review_difference_is_substantive(prior_difference)
            )
        )
        if is_valid and independent:
            is_valid = (
                summary.quality is not EvidenceQuality.INVALID
                and account.quality is not EvidenceQuality.INVALID
                and set(summary.classification_facts)
                != set(account.classification_facts)
            )
        if is_valid and summary.candidate_item_ids and account.candidate_item_ids:
            if not set(summary.candidate_item_ids).intersection(account.candidate_item_ids):
                is_valid = summary.conflict or account.conflict
        if not is_valid:
            invalid.add(task_id)
            continue
        result = StructuredAIResult(
            task_id=task_id,
            component_id=str(component_id),
            summary=summary,
            account_path=account,
            sources_independent=bool(independent),
            business_conflict=bool(business_conflict),
            direction_status=str(direction_status),
            reason=reason.strip(),
            alternative_item_ids=tuple(str(item) for item in alternatives),
            note_ids=tuple(str(item) for item in note_ids),
            review_round=str(review_round),
            reviewer_id=str(reviewer_id).strip(),
            model_id=str(model_id).strip(),
            reviewed_at=str(reviewed_at),
            prior_result_difference=str(prior_difference).strip(),
        )
        # 结构校验和证据重算必须使用同一套独立性约束；事实仅为另一来源
        # 子集时，不得等到正式导入阶段才因“虚假独立”抛错。
        try:
            recalculate_ai_evidence(result)
        except ValueError:
            invalid.add(task_id)
            continue
        valid.append(result)

    valid_by_component: dict[str, list[StructuredAIResult]] = {}
    for result in valid:
        valid_by_component.setdefault(result.component_id, []).append(result)
    for component_results in valid_by_component.values():
        rounds = {result.review_round for result in component_results}
        blind_rounds = rounds.intersection({"A", "B", "C"})
        if len(blind_rounds) >= 2 and len(
            {
                result.reviewer_id
                for result in component_results
                if result.review_round in blind_rounds
            }
        ) != len(blind_rounds):
            invalid.update(
                result.task_id
                for result in component_results
                if result.review_round in blind_rounds
            )
    valid = [result for result in valid if result.task_id not in invalid]
    valid_ids = {item.task_id for item in valid}
    missing = tuple(sorted(set(expected_by_id) - valid_ids))
    status = "AI 已完成" if not missing and not duplicate_ids and not invalid else "AI 未完成"
    return StructuredAIValidation(
        tuple(valid),
        missing,
        duplicate_ids,
        tuple(sorted(invalid)),
        status,
    )


def merge_structured_ai_results(
    expected: Sequence[AITask],
    prior_results: Sequence[StructuredAIResult],
    payloads: Sequence[Mapping[str, object]],
    valid_item_ids: set[str],
) -> StructuredAIValidation:
    incoming = validate_structured_ai_results(expected, payloads, valid_item_ids)
    merged = {item.task_id: item for item in prior_results}
    prior_blind_reviewers = {
        item.reviewer_id: item.task_id
        for item in prior_results
        if item.review_round in {"A", "B", "C"}
    }
    cross_batch_invalid = {
        item.task_id
        for item in incoming.valid_results
        if item.review_round in {"A", "B", "C"}
        and item.reviewer_id in prior_blind_reviewers
        and prior_blind_reviewers[item.reviewer_id] != item.task_id
    }
    for item in incoming.valid_results:
        if item.task_id in cross_batch_invalid:
            continue
        prior = merged.get(item.task_id)
        if prior is not None and prior != item:
            raise ValueError(f"AI任务 {item.task_id} 与已导入结果冲突")
        merged[item.task_id] = item
    ordered = tuple(
        merged[task.task_id] for task in expected if task.task_id in merged
    )
    missing = tuple(task.task_id for task in expected if task.task_id not in merged)
    invalid_ids = tuple(
        sorted(set(incoming.invalid_ids).union(cross_batch_invalid))
    )
    status = (
        "AI 已完成"
        if not missing and not incoming.duplicate_ids and not invalid_ids
        else "AI 未完成"
    )
    return StructuredAIValidation(
        ordered,
        missing,
        incoming.duplicate_ids,
        invalid_ids,
        status,
    )


def recalculate_ai_evidence(result: StructuredAIResult):
    summary = EvidenceSourceAssessment(
        EvidenceSource.SUMMARY,
        result.summary.candidate_item_id,
        result.summary.quality,
        result.summary.basis_text,
        result.summary.classification_facts,
        result.summary.candidate_item_ids,
    )
    account_path = EvidenceSourceAssessment(
        EvidenceSource.ACCOUNT_PATH,
        result.account_path.candidate_item_id,
        result.account_path.quality,
        result.account_path.basis_text,
        result.account_path.classification_facts,
        result.account_path.candidate_item_ids,
    )
    return combine_source_assessments(
        summary,
        account_path,
        sources_independent=result.sources_independent,
    )


def _review_policy(
    decision: ClassificationDecision,
    materiality: MaterialityLevel,
    has_valid_original: bool,
) -> str:
    if decision.ai_review_policy:
        return decision.ai_review_policy
    if decision.decision_action == DecisionAction.AI_DOUBLE_FOLLOWUP_REVIEW.value:
        return "valid_original_retention"
    if decision.decision_action == DecisionAction.AI_THIRD_REVIEW.value:
        return "blank_low_majority"
    score = decision.evidence_score
    if has_valid_original:
        if materiality is MaterialityLevel.M2 and (score is None or score <= 50):
            return "valid_original_retention"
        return "valid_original_change"
    if score is None or score <= 50:
        return (
            "blank_low_single"
            if materiality is MaterialityLevel.M0
            else "blank_low_majority"
        )
    if score == 55:
        return (
            "blank_55_double"
            if materiality is MaterialityLevel.M2
            else "blank_55_single"
        )
    if score == 70:
        return "blank_70_single"
    return "blank_90_single"


def _apply_ai_outcome(
    decision: ClassificationDecision,
    *,
    action: DecisionAction,
    candidate_id: str,
    score: int | None,
    selected_result: StructuredAIResult | None,
    selected_assessment,
    item_names: Mapping[str, str],
    item_directions: Mapping[str, str],
    source_conflict: bool = False,
    business_conflict: bool = False,
    direction_status: str = "compatible",
    decision_source: str,
    review_policy: str,
) -> ClassificationDecision:
    if action is DecisionAction.AUTOMATIC_KEEP:
        candidate_id = decision.original_standard_item_id
    resolved = action in {
        DecisionAction.AUTOMATIC_KEEP,
        DecisionAction.AUTOMATIC_FILL,
        DecisionAction.AUTOMATIC_CHANGE,
    }
    if selected_result is None or selected_assessment is None:
        summary_quality = decision.summary_quality
        account_quality = decision.account_path_quality
        independent = False
        evidence_sources = decision.evidence_sources
        summary_candidates = decision.summary_candidate_item_ids or ()
        account_path_candidates = decision.account_path_candidate_item_ids or ()
        summary_preferred = decision.summary_preferred_item_id
        account_path_preferred = decision.account_path_preferred_item_id
    else:
        summary_quality = selected_result.summary.quality.value
        account_quality = selected_result.account_path.quality.value
        independent = selected_assessment.sources_independent
        summary_candidates = selected_result.summary.candidate_item_ids
        account_path_candidates = selected_result.account_path.candidate_item_ids
        summary_preferred = selected_result.summary.candidate_item_id
        account_path_preferred = selected_result.account_path.candidate_item_id
        evidence_sources = tuple(
            source.value
            for source, quality in (
                (EvidenceSource.SUMMARY, selected_result.summary.quality),
                (EvidenceSource.ACCOUNT_PATH, selected_result.account_path.quality),
            )
            if quality is not EvidenceQuality.INVALID
        )
    evidence_level = (
        "invalid"
        if score in {None, 0}
        else "strong"
        if score >= 70
        else "medium"
        if score >= 45
        else "weak"
    )
    original_state = OriginalItemState(decision.original_item_state)
    if action is DecisionAction.AUTOMATIC_KEEP:
        original_state = OriginalItemState.AGREES
    elif candidate_id and decision.original_standard_item_id:
        original_state = (
            OriginalItemState.AGREES
            if candidate_id == decision.original_standard_item_id
            else OriginalItemState.CONFLICTS
        )
    return replace(
        decision,
        system_item_id=candidate_id,
        system_item_name=item_names.get(candidate_id, decision.system_item_name),
        normal_direction=item_directions.get(candidate_id, decision.normal_direction),
        reason=(
            f"{decision.reason}；AI仅重新解释原始摘要和完整路径；"
            f"系统重算证据分数为{'无可用分数' if score is None else score}；"
            f"后续动作：{action.value}"
        ),
        evidence_level=evidence_level,
        resolved=resolved,
        decision_source=decision_source,
        evidence_score=score,
        evidence_sources=evidence_sources,
        candidate_item_ids=tuple(
            dict.fromkeys((*summary_candidates, *account_path_candidates))
        ),
        summary_candidate_item_ids=summary_candidates,
        account_path_candidate_item_ids=account_path_candidates,
        summary_preferred_item_id=summary_preferred,
        account_path_preferred_item_id=account_path_preferred,
        summary_quality=summary_quality,
        account_path_quality=account_quality,
        sources_independent=independent,
        source_conflict=source_conflict,
        business_conflict=business_conflict,
        direction_status=direction_status,
        decision_action=action.value,
        decision_rule_id=f"POLICY-AI-OUTCOME:{review_policy}:{action.value}",
        ai_review_policy=review_policy,
        original_item_state=original_state.value,
        candidate_status="available" if candidate_id else decision.candidate_status,
    )


def resolve_structured_ai_results(
    decisions: Sequence[ClassificationDecision],
    tasks: Sequence[AITask],
    results: Sequence[StructuredAIResult],
    item_names: Mapping[str, str],
    item_directions: Mapping[str, str],
    *,
    failed_task_ids: set[str] | frozenset[str] = frozenset(),
    automatic_change_threshold: int = DEFAULT_AUTOMATIC_CHANGE_SCORE,
) -> tuple[ClassificationDecision, ...]:
    """把AI对两个原始来源的解释交回系统，按既定门槛形成唯一后续动作。"""
    automatic_change_threshold = validate_automatic_change_threshold(
        automatic_change_threshold
    )
    tasks_by_component: dict[str, list[AITask]] = {}
    for task in tasks:
        tasks_by_component.setdefault(task.component_id, []).append(task)
    result_by_task = {result.task_id: result for result in results}
    routed: list[ClassificationDecision] = []
    ai_actions = {
        DecisionAction.AI_REVIEW.value,
        DecisionAction.DOUBLE_AI_REVIEW.value,
        DecisionAction.AI_DOUBLE_FOLLOWUP_REVIEW.value,
        DecisionAction.AI_THIRD_REVIEW.value,
    }
    for decision in decisions:
        if decision.decision_action not in ai_actions:
            routed.append(decision)
            continue
        component_tasks = tasks_by_component.get(decision.component_id, [])
        component_results = [
            result_by_task[task.task_id]
            for task in component_tasks
            if task.task_id in result_by_task
        ]
        action_kind = DecisionAction(decision.decision_action)
        if action_kind in {
            DecisionAction.DOUBLE_AI_REVIEW,
            DecisionAction.AI_DOUBLE_FOLLOWUP_REVIEW,
        }:
            tagged_tasks = [
                task
                for task in component_tasks
                if "独立复核A" in task.context or "独立复核B" in task.context
            ]
            tagged_results = [
                result for result in component_results if result.review_round in {"A", "B"}
            ]
            component_tasks = tagged_tasks or component_tasks[-2:]
            component_results = tagged_results or component_results[-2:]
        elif action_kind is DecisionAction.AI_THIRD_REVIEW:
            tagged_tasks = [
                task
                for task in component_tasks
                if any(f"独立复核{slot}" in task.context for slot in ("A", "B", "C"))
            ]
            tagged_results = [
                result
                for result in component_results
                if result.review_round in {"A", "B", "C"}
            ]
            component_tasks = tagged_tasks or component_tasks[-3:]
            component_results = tagged_results or component_results[-3:]
        component_failed_ids = {
            task.task_id for task in component_tasks if task.task_id in failed_task_ids
        }
        required_count = (
            3
            if action_kind is DecisionAction.AI_THIRD_REVIEW
            else 2
            if action_kind
            in {
                DecisionAction.DOUBLE_AI_REVIEW,
                DecisionAction.AI_DOUBLE_FOLLOWUP_REVIEW,
            }
            else 1
        )
        if len(component_tasks) != required_count or (
            len(component_results) + len(component_failed_ids) != required_count
        ):
            raise ValueError(f"AI复核结果不完整：{decision.component_id}")

        assessments = [recalculate_ai_evidence(result) for result in component_results]
        candidate_ids = {
            assessment.candidate_item_id for assessment in assessments
        }
        # A、B的一致性看候选是否相同，不要求内部质量组合或总分相同。
        # 各行动分支随后分别检查每份结果是否达到本格最低门槛。
        review_disagreement = len(candidate_ids) != 1
        source_conflict = review_disagreement or any(
            assessment.conflict for assessment in assessments
        )
        business_conflict = any(result.business_conflict for result in component_results)
        direction_statuses = {result.direction_status for result in component_results}
        direction_status = (
            direction_statuses.pop()
            if len(direction_statuses) == 1
            else "incompatible"
        )

        usable = [
            (assessment, result)
            for assessment, result in zip(assessments, component_results)
            if assessment.score is not None and assessment.candidate_item_id
        ]
        chosen_assessment = None
        chosen_result = None
        if not source_conflict and usable:
            chosen_assessment, chosen_result = min(
                usable,
                key=lambda pair: (
                    pair[0].score,
                    pair[1].summary.quality.value,
                    pair[1].account_path.quality.value,
                    pair[1].task_id,
                ),
            )

        try:
            materiality = MaterialityLevel(decision.materiality_level)
        except ValueError as error:
            raise ValueError(
                f"AI复核决定缺少有效重要性层级：{decision.component_id}"
            ) from error
        original_state = OriginalItemState(decision.original_item_state)
        has_valid_original = bool(decision.original_standard_item_id) and original_state in {
            OriginalItemState.AGREES,
            OriginalItemState.CONFLICTS,
            OriginalItemState.PENDING_COMPARISON,
        }
        review_policy = _review_policy(decision, materiality, has_valid_original)
        valid_votes = [
            (assessment, result)
            for assessment, result in zip(assessments, component_results)
            if assessment.score is not None
            and assessment.candidate_item_id
            and not assessment.conflict
            and not result.business_conflict
            and result.direction_status == "compatible"
        ]

        if action_kind is DecisionAction.AI_THIRD_REVIEW:
            vote_counts = Counter(
                assessment.candidate_item_id for assessment, _ in valid_votes
            )
            majority = next(
                (
                    candidate_id
                    for candidate_id, count in vote_counts.items()
                    if count >= 2
                ),
                "",
            )
            majority_votes = [
                pair
                for pair in valid_votes
                if pair[0].candidate_item_id == majority
            ]
            if majority_votes:
                chosen_assessment, chosen_result = min(
                    majority_votes,
                    key=lambda pair: (
                        pair[0].score,
                        pair[1].summary.quality.value,
                        pair[1].account_path.quality.value,
                        pair[1].task_id,
                    ),
                )
                next_action = DecisionAction.AUTOMATIC_FILL
                decision_source = "ai_majority_decision"
                candidate_id = majority
                score = chosen_assessment.score
            else:
                chosen_assessment = chosen_result = None
                next_action = _AI_UNRESOLVED_ACTION
                decision_source = "ai_review_pending_human"
                candidate_id = decision.system_item_id
                score = None
            routed.append(
                _apply_ai_outcome(
                    decision,
                    action=next_action,
                    candidate_id=candidate_id,
                    score=score,
                    selected_result=chosen_result,
                    selected_assessment=chosen_assessment,
                    item_names=item_names,
                    item_directions=item_directions,
                    source_conflict=not bool(majority_votes),
                    decision_source=decision_source,
                    review_policy=review_policy,
                )
            )
            continue

        if (
            action_kind is DecisionAction.DOUBLE_AI_REVIEW
            and review_policy == "individual_tax_service"
        ):
            same_candidate = bool(valid_votes) and len(valid_votes) == 2 and len(
                {assessment.candidate_item_id for assessment, _ in valid_votes}
            ) == 1
            chosen_assessment = chosen_result = None
            if same_candidate:
                chosen_assessment, chosen_result = min(
                    valid_votes,
                    key=lambda pair: (
                        pair[0].score,
                        pair[1].summary.quality.value,
                        pair[1].account_path.quality.value,
                        pair[1].task_id,
                    ),
                )
            if not same_candidate:
                next_action = _AI_UNRESOLVED_ACTION
                candidate_id = decision.system_item_id
                score = None
                decision_source = "ai_review_pending_human"
            elif has_valid_original:
                can_change = bool(
                    chosen_assessment is not None
                    and chosen_assessment.candidate_item_id
                    != decision.original_standard_item_id
                    and all(
                        assessment.score is not None
                        and score_meets_change_threshold(
                            assessment.score,
                            automatic_change_threshold,
                            result.summary.quality.value,
                            result.account_path.quality.value,
                        )
                        for assessment, result in valid_votes
                    )
                )
                next_action = (
                    DecisionAction.AUTOMATIC_CHANGE
                    if can_change
                    else DecisionAction.AUTOMATIC_KEEP
                )
                candidate_id = (
                    chosen_assessment.candidate_item_id
                    if can_change and chosen_assessment is not None
                    else decision.original_standard_item_id
                )
                score = chosen_assessment.score if chosen_assessment else None
                decision_source = (
                    "ai_blind_consensus_decision"
                    if can_change
                    else "ai_reviewed_original_kept"
                )
            else:
                next_action = DecisionAction.AUTOMATIC_FILL
                candidate_id = chosen_assessment.candidate_item_id
                score = chosen_assessment.score
                decision_source = "ai_blind_consensus_decision"
            routed.append(
                _apply_ai_outcome(
                    decision,
                    action=next_action,
                    candidate_id=candidate_id,
                    score=score,
                    selected_result=chosen_result,
                    selected_assessment=chosen_assessment,
                    item_names=item_names,
                    item_directions=item_directions,
                    source_conflict=not same_candidate,
                    decision_source=decision_source,
                    review_policy=review_policy,
                )
            )
            continue

        if review_policy in {"direction_compatibility", "reversal_one_time"} and action_kind in {
            DecisionAction.AI_REVIEW,
            DecisionAction.DOUBLE_AI_REVIEW,
        }:
            required_votes = (
                2 if action_kind is DecisionAction.DOUBLE_AI_REVIEW else 1
            )
            same_candidate = bool(valid_votes) and len(valid_votes) == required_votes and len(
                {assessment.candidate_item_id for assessment, _ in valid_votes}
            ) == 1
            chosen_assessment = chosen_result = None
            if same_candidate:
                chosen_assessment, chosen_result = min(
                    valid_votes,
                    key=lambda pair: (
                        pair[0].score,
                        pair[1].summary.quality.value,
                        pair[1].account_path.quality.value,
                        pair[1].task_id,
                    ),
                )
            if not same_candidate:
                next_action = _AI_UNRESOLVED_ACTION
                candidate_id = decision.system_item_id
                score = None
                decision_source = "ai_review_pending_human"
            elif not has_valid_original:
                next_action = DecisionAction.AUTOMATIC_FILL
                candidate_id = chosen_assessment.candidate_item_id
                score = chosen_assessment.score
                decision_source = "ai_reviewed_system_decision"
            elif chosen_assessment.candidate_item_id == decision.original_standard_item_id:
                next_action = DecisionAction.AUTOMATIC_KEEP
                candidate_id = decision.original_standard_item_id
                score = chosen_assessment.score
                decision_source = "ai_reviewed_original_kept"
            else:
                can_change = all(
                    assessment.score is not None
                    and score_meets_change_threshold(
                        assessment.score,
                        automatic_change_threshold,
                        result.summary.quality.value,
                        result.account_path.quality.value,
                    )
                    for assessment, result in valid_votes
                )
                next_action = (
                    DecisionAction.AUTOMATIC_CHANGE
                    if can_change
                    else _AI_UNRESOLVED_ACTION
                )
                candidate_id = (
                    chosen_assessment.candidate_item_id
                    if can_change
                    else decision.system_item_id
                )
                score = chosen_assessment.score if can_change else None
                decision_source = (
                    "ai_reviewed_system_decision"
                    if can_change
                    else "ai_review_pending_human"
                )
            routed.append(
                _apply_ai_outcome(
                    decision,
                    action=next_action,
                    candidate_id=candidate_id,
                    score=score,
                    selected_result=chosen_result,
                    selected_assessment=chosen_assessment,
                    item_names=item_names,
                    item_directions=item_directions,
                    source_conflict=not same_candidate,
                    direction_status=(
                        chosen_result.direction_status
                        if chosen_result is not None
                        else "incompatible"
                    ),
                    decision_source=decision_source,
                    review_policy=review_policy,
                )
            )
            continue

        if action_kind in {
            DecisionAction.DOUBLE_AI_REVIEW,
            DecisionAction.AI_DOUBLE_FOLLOWUP_REVIEW,
        } and review_policy in {
            "blank_low_majority",
            "blank_55_double",
            "valid_original_retention",
            "valid_original_change",
        }:
            same_candidate = bool(valid_votes) and len(valid_votes) == 2 and len(
                {assessment.candidate_item_id for assessment, _ in valid_votes}
            ) == 1
            chosen_assessment = chosen_result = None
            if same_candidate:
                chosen_assessment, chosen_result = min(
                    valid_votes,
                    key=lambda pair: (
                        pair[0].score,
                        pair[1].summary.quality.value,
                        pair[1].account_path.quality.value,
                        pair[1].task_id,
                    ),
                )
            if review_policy == "blank_low_majority":
                if same_candidate:
                    next_action = DecisionAction.AUTOMATIC_FILL
                    candidate_id = chosen_assessment.candidate_item_id
                    score = chosen_assessment.score
                    decision_source = "ai_blind_consensus_decision"
                else:
                    next_action = DecisionAction.AI_THIRD_REVIEW
                    candidate_id = decision.system_item_id
                    score = None
                    decision_source = "ai_review_waiting_third"
            elif review_policy == "blank_55_double":
                can_fill = bool(
                    same_candidate
                    and chosen_assessment is not None
                    and chosen_assessment.score is not None
                    and chosen_assessment.score >= 55
                )
                next_action = (
                    DecisionAction.AUTOMATIC_FILL
                    if can_fill
                    else _AI_UNRESOLVED_ACTION
                )
                candidate_id = (
                    chosen_assessment.candidate_item_id
                    if can_fill and chosen_assessment is not None
                    else decision.system_item_id
                )
                score = chosen_assessment.score if can_fill else None
                decision_source = (
                    "ai_blind_consensus_decision"
                    if can_fill
                    else "ai_review_pending_human"
                )
            else:
                can_change = bool(
                    same_candidate
                    and chosen_assessment is not None
                    and all(
                        assessment.score is not None
                        and score_meets_change_threshold(
                            assessment.score,
                            automatic_change_threshold,
                            result.summary.quality.value,
                            result.account_path.quality.value,
                        )
                        for assessment, result in valid_votes
                    )
                    and chosen_assessment.candidate_item_id
                    != decision.original_standard_item_id
                )
                next_action = (
                    DecisionAction.AUTOMATIC_CHANGE
                    if can_change
                    else DecisionAction.AUTOMATIC_KEEP
                )
                candidate_id = (
                    chosen_assessment.candidate_item_id
                    if can_change and chosen_assessment is not None
                    else decision.original_standard_item_id
                )
                score = chosen_assessment.score if can_change else None
                decision_source = (
                    "ai_blind_consensus_decision"
                    if can_change
                    else "ai_reviewed_original_kept"
                )
            routed.append(
                _apply_ai_outcome(
                    decision,
                    action=next_action,
                    candidate_id=candidate_id,
                    score=score,
                    selected_result=chosen_result,
                    selected_assessment=chosen_assessment,
                    item_names=item_names,
                    item_directions=item_directions,
                    source_conflict=not same_candidate,
                    decision_source=decision_source,
                    review_policy=review_policy,
                )
            )
            continue

        if action_kind is DecisionAction.AI_REVIEW and review_policy in {
            "valid_original_retention",
            "valid_original_change",
            "blank_low_single",
            "blank_55_single",
            "blank_70_single",
            "blank_90_single",
        }:
            chosen_assessment, chosen_result = (
                valid_votes[0] if len(valid_votes) == 1 else (None, None)
            )
            if review_policy == "valid_original_retention":
                no_modification_claim = bool(
                    chosen_assessment is None
                    and not source_conflict
                    and not business_conflict
                    and direction_status == "compatible"
                )
                confirms_keep = bool(
                    no_modification_claim
                    or (
                        chosen_assessment is not None
                        and chosen_assessment.candidate_item_id
                        == decision.original_standard_item_id
                    )
                )
                next_action = (
                    DecisionAction.AUTOMATIC_KEEP
                    if confirms_keep
                    else DecisionAction.AI_DOUBLE_FOLLOWUP_REVIEW
                )
                candidate_id = (
                    decision.original_standard_item_id
                    if confirms_keep
                    else (
                        chosen_assessment.candidate_item_id
                        if chosen_assessment is not None
                        else decision.system_item_id
                    )
                )
                score = (
                    chosen_assessment.score
                    if chosen_assessment is not None
                    else None
                )
                decision_source = (
                    "ai_reviewed_original_kept"
                    if confirms_keep
                    else "ai_review_waiting_blind_double"
                )
            elif review_policy == "valid_original_change":
                can_change = bool(
                    chosen_assessment is not None
                    and chosen_assessment.score is not None
                    and score_meets_change_threshold(
                        chosen_assessment.score,
                        automatic_change_threshold,
                        (
                            chosen_result.summary.quality.value
                            if chosen_result is not None
                            else 0
                        ),
                        (
                            chosen_result.account_path.quality.value
                            if chosen_result is not None
                            else 0
                        ),
                    )
                    and chosen_assessment.candidate_item_id == decision.system_item_id
                    and chosen_assessment.candidate_item_id
                    != decision.original_standard_item_id
                )
                next_action = (
                    DecisionAction.AUTOMATIC_CHANGE
                    if can_change
                    else DecisionAction.AUTOMATIC_KEEP
                )
                candidate_id = (
                    chosen_assessment.candidate_item_id
                    if can_change and chosen_assessment is not None
                    else decision.original_standard_item_id
                )
                score = (
                    chosen_assessment.score
                    if chosen_assessment is not None
                    else None
                )
                decision_source = (
                    "ai_reviewed_system_decision"
                    if can_change
                    else "ai_reviewed_original_kept"
                )
            else:
                minimum = _BLANK_SINGLE_MINIMUM_SCORES[review_policy]
                can_fill = bool(
                    chosen_assessment is not None
                    and chosen_assessment.score is not None
                    and chosen_assessment.score >= minimum
                )
                if can_fill:
                    next_action = DecisionAction.AUTOMATIC_FILL
                    candidate_id = chosen_assessment.candidate_item_id
                    score = chosen_assessment.score
                    decision_source = "ai_reviewed_system_decision"
                elif materiality in {MaterialityLevel.M1, MaterialityLevel.M2}:
                    next_action = DecisionAction.DOUBLE_AI_REVIEW
                    candidate_id = decision.system_item_id
                    score = None
                    decision_source = "ai_review_waiting_blind_double"
                    review_policy = "blank_low_majority"
                else:
                    next_action = _AI_UNRESOLVED_ACTION
                    candidate_id = decision.system_item_id
                    score = None
                    decision_source = "ai_review_pending_human"
            routed.append(
                _apply_ai_outcome(
                    decision,
                    action=next_action,
                    candidate_id=candidate_id,
                    score=score,
                    selected_result=chosen_result,
                    selected_assessment=chosen_assessment,
                    item_names=item_names,
                    item_directions=item_directions,
                    source_conflict=chosen_assessment is None,
                    decision_source=decision_source,
                    review_policy=review_policy,
                )
            )
            continue

        raise RuntimeError(
            "分类AI动作没有命中正式行动表："
            f"{decision.component_id}（{action_kind.value}，{review_policy}）"
        )
    return tuple(routed)


def write_ai_tasks_jsonl(path: Path, tasks: Sequence[AITask]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="\n") as output:
        for task in tasks:
            payload = asdict(task)
            for field in (
                "original_item",
                "system_item_id",
                "rule_evidence",
                "candidate_item_ids",
                "summary_candidate_item_ids",
                "account_path_candidate_item_ids",
            ):
                payload.pop(field, None)
            output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
