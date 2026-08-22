from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class EvidenceQuality(IntEnum):
    INVALID = 0
    WEAK = 10
    MEDIUM = 25
    STRONG = 45


class EvidenceSource(StrEnum):
    SUMMARY = "summary"
    ACCOUNT_PATH = "account_path"


class OriginalItemState(StrEnum):
    AGREES = "agrees"
    BLANK = "blank"
    UNSTANDARDIZABLE = "unstandardizable"
    CONFLICTS = "conflicts"
    PENDING_COMPARISON = "pending_comparison"


class MaterialityLevel(StrEnum):
    M0 = "M0"
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"


class DecisionOperation(StrEnum):
    KEEP = "keep"
    FILL = "fill"
    CHANGE = "change"


class DecisionAction(StrEnum):
    AUTOMATIC_KEEP = "automatic_keep"
    AUTOMATIC_FILL = "automatic_fill"
    AUTOMATIC_CHANGE = "automatic_change"
    AI_REVIEW = "ai_review"
    DOUBLE_AI_REVIEW = "double_ai_review"
    AI_DOUBLE_FOLLOWUP_REVIEW = "ai_double_followup_review"
    AI_THIRD_REVIEW = "ai_third_review"
    LOW_AMOUNT_HUMAN_BATCH = "low_amount_human_batch"
    HUMAN_BATCH = "human_batch"
    HUMAN_DECISION = "human_decision"
    CONFIRM_REVERSAL_RULE = "confirm_reversal_rule"
    ISOLATE_INVALID_INPUT = "isolate_invalid_input"
    CONFIRM_CASH_SCOPE = "confirm_cash_scope"


@dataclass(frozen=True, slots=True)
class EvidenceSourceAssessment:
    source: EvidenceSource
    candidate_item_id: str
    quality: EvidenceQuality
    basis_text: str
    classification_facts: tuple[str, ...] = ()
    candidate_item_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        candidates = tuple(
            dict.fromkeys(
                self.candidate_item_ids
                or ((self.candidate_item_id,) if self.candidate_item_id else ())
            )
        )
        if self.candidate_item_id and self.candidate_item_id not in candidates:
            raise ValueError("单一候选必须包含在候选集合中")
        if self.quality is EvidenceQuality.INVALID:
            if candidates or self.classification_facts:
                raise ValueError("无效来源不能携带候选项目或分类事实")
            return
        if not candidates:
            raise ValueError("有效来源必须至少有一个候选项目")
        if len(candidates) > 1 and self.quality is not EvidenceQuality.WEAK:
            raise ValueError("单个来源支持多个候选时最高只能评弱")
        if not self.classification_facts:
            raise ValueError("有效来源必须有分类相关事实")
        object.__setattr__(self, "candidate_item_ids", candidates)


@dataclass(frozen=True, slots=True)
class EvidenceAssessment:
    candidate_item_id: str
    score: int | None
    independent_source_count: int
    sources_independent: bool
    conflict: bool
    conflict_item_ids: tuple[str, ...] = ()
    candidate_item_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionRoute:
    action: DecisionAction
    allowed_operations: frozenset[DecisionOperation]
    required_post_review_score: int | None = None
    forced_check: str = ""
    review_policy: str = ""


_GENERIC_FACT_PREFIXES = ("cash_direction:",)
_ALLOWED_SCORES = frozenset({0, 10, 20, 25, 35, 45, 50, 55, 70, 90})
_LEVEL_INDEX = {
    MaterialityLevel.M0: 0,
    MaterialityLevel.M1: 1,
    MaterialityLevel.M2: 2,
    MaterialityLevel.M3: 3,
}

_AGREEMENT_ACTIONS = {
    score: (
        DecisionAction.AUTOMATIC_KEEP,
        DecisionAction.AUTOMATIC_KEEP,
        (
            DecisionAction.AI_REVIEW
            if score in {0, 10, 20, 25, 35, 45, 50}
            else DecisionAction.AUTOMATIC_KEEP
        ),
        DecisionAction.HUMAN_DECISION,
    )
    for score in _ALLOWED_SCORES
}

_OTHER_ACTIONS = {
    score: (
        DecisionAction.AI_REVIEW,
        DecisionAction.DOUBLE_AI_REVIEW,
        DecisionAction.DOUBLE_AI_REVIEW,
        DecisionAction.HUMAN_DECISION,
    )
    for score in {0, 10, 20, 25, 35, 45, 50}
}
_OTHER_ACTIONS.update(
    {
        55: (
            DecisionAction.AI_REVIEW,
            DecisionAction.AI_REVIEW,
            DecisionAction.DOUBLE_AI_REVIEW,
            DecisionAction.HUMAN_DECISION,
        ),
        70: (
            DecisionAction.AI_REVIEW,
            DecisionAction.AI_REVIEW,
            DecisionAction.AI_REVIEW,
            DecisionAction.HUMAN_DECISION,
        ),
        90: (
            DecisionAction.AI_REVIEW,
            DecisionAction.AI_REVIEW,
            DecisionAction.AI_REVIEW,
            DecisionAction.AI_REVIEW,
        ),
    }
)

_VALID_ORIGINAL_CHANGE_ACTIONS = {
    score: (
        DecisionAction.AUTOMATIC_KEEP,
        DecisionAction.AUTOMATIC_KEEP,
        (
            DecisionAction.AI_REVIEW
            if score in {0, 10, 20, 25, 35, 45, 50}
            else DecisionAction.AUTOMATIC_KEEP
        ),
        DecisionAction.HUMAN_DECISION,
    )
    for score in {0, 10, 20, 25, 35, 45, 50, 55}
}
_VALID_ORIGINAL_CHANGE_ACTIONS.update(
    {
        score: (
            DecisionAction.AUTOMATIC_CHANGE,
            DecisionAction.AI_REVIEW,
            DecisionAction.DOUBLE_AI_REVIEW,
            DecisionAction.HUMAN_DECISION,
        )
        for score in {70, 90}
    }
)

_VALID_ORIGINAL_STATES = frozenset(
    {
        OriginalItemState.AGREES,
        OriginalItemState.CONFLICTS,
        OriginalItemState.PENDING_COMPARISON,
    }
)


def _specific_facts(source: EvidenceSourceAssessment) -> frozenset[str]:
    normalized: set[str] = set()
    source_prefixes = {
        "summary",
        "summary_fact",
        "account",
        "account_fact",
        "account_path",
        "path",
        "source",
    }
    for fact in source.classification_facts:
        if fact.startswith(_GENERIC_FACT_PREFIXES):
            continue
        prefix, separator, value = fact.partition(":")
        normalized.add(value if separator and prefix in source_prefixes else fact)
    return frozenset(normalized)


def _are_independent(
    summary: EvidenceSourceAssessment,
    account_path: EvidenceSourceAssessment,
) -> bool:
    summary_facts = _specific_facts(summary)
    path_facts = _specific_facts(account_path)
    return bool(summary_facts - path_facts)


def combine_source_assessments(
    summary: EvidenceSourceAssessment,
    account_path: EvidenceSourceAssessment,
    sources_independent: bool | None = None,
) -> EvidenceAssessment:
    if summary.source is not EvidenceSource.SUMMARY:
        raise ValueError("第一项必须是摘要来源")
    if account_path.source is not EvidenceSource.ACCOUNT_PATH:
        raise ValueError("第二项必须是完整对方科目路径来源")

    valid = tuple(
        source
        for source in (summary, account_path)
        if source.quality is not EvidenceQuality.INVALID
    )
    if not valid:
        return EvidenceAssessment("", 0, 0, False, False, (), ())
    if len(valid) == 1:
        source = valid[0]
        candidates = source.candidate_item_ids
        return EvidenceAssessment(
            candidates[0] if len(candidates) == 1 else "",
            source.quality.value,
            1,
            False,
            False,
            (),
            candidates,
        )

    summary_candidates = set(summary.candidate_item_ids)
    path_candidates = set(account_path.candidate_item_ids)
    common_item_ids = tuple(sorted(summary_candidates & path_candidates))
    if not common_item_ids:
        conflict_item_ids = tuple(sorted(summary_candidates | path_candidates))
        return EvidenceAssessment(
            "", None, 2, True, True, conflict_item_ids, ()
        )

    calculated_independence = _are_independent(summary, account_path)
    if sources_independent is True and not calculated_independence:
        raise ValueError("两个来源没有提供不同的分类相关事实，不能声明为独立")
    independent = (
        calculated_independence
        if sources_independent is None
        else sources_independent and calculated_independence
    )
    if independent:
        score = summary.quality.value + account_path.quality.value
        return EvidenceAssessment(
            common_item_ids[0] if len(common_item_ids) == 1 else "",
            score,
            2,
            True,
            False,
            (),
            common_item_ids,
        )

    score = max(summary.quality.value, account_path.quality.value)
    return EvidenceAssessment(
        common_item_ids[0] if len(common_item_ids) == 1 else "",
        score,
        1,
        False,
        False,
        (),
        common_item_ids,
    )


def materiality_level(amount_cent: int, thresholds) -> MaterialityLevel:
    amount = abs(amount_cent)
    if amount >= thresholds.overall_cent:
        return MaterialityLevel.M3
    if amount >= thresholds.performance_cent:
        return MaterialityLevel.M2
    if amount >= thresholds.trivial_cent:
        return MaterialityLevel.M1
    return MaterialityLevel.M0


def effective_materiality(
    single_amount_cent: int,
    same_class_total_cent: int,
    thresholds,
) -> MaterialityLevel:
    return materiality_level(single_amount_cent, thresholds)


def _allowed_operations(score: int) -> frozenset[DecisionOperation]:
    if score in {45, 50, 55}:
        return frozenset({DecisionOperation.KEEP})
    if score in {70, 90}:
        return frozenset(DecisionOperation)
    return frozenset()


def route_normal_decision(
    score: int,
    original_state: OriginalItemState,
    materiality: MaterialityLevel,
) -> DecisionRoute:
    if score not in _ALLOWED_SCORES:
        raise ValueError(f"不允许的证据分数：{score}")

    agrees = original_state is OriginalItemState.AGREES
    if agrees:
        table = _AGREEMENT_ACTIONS
    elif original_state in _VALID_ORIGINAL_STATES:
        table = _VALID_ORIGINAL_CHANGE_ACTIONS
    else:
        table = _OTHER_ACTIONS
    action = table[score][_LEVEL_INDEX[materiality]]
    if action is DecisionAction.AUTOMATIC_FILL and original_state is OriginalItemState.CONFLICTS:
        action = DecisionAction.AUTOMATIC_CHANGE

    required_score = None
    review_policy = ""
    if original_state in _VALID_ORIGINAL_STATES:
        if materiality is MaterialityLevel.M2 and score <= 50:
            review_policy = "valid_original_retention"
            required_score = 70
        elif action in {DecisionAction.AI_REVIEW, DecisionAction.DOUBLE_AI_REVIEW}:
            review_policy = "valid_original_change"
            required_score = 70
    elif score <= 50:
        review_policy = (
            "blank_low_single"
            if materiality is MaterialityLevel.M0
            else "blank_low_majority"
        )
        required_score = 0
    elif score == 55:
        review_policy = (
            "blank_55_double"
            if materiality is MaterialityLevel.M2
            else "blank_55_single"
        )
        required_score = 55
    elif score == 70:
        review_policy = "blank_70_single"
        required_score = 70
    elif score == 90:
        review_policy = "blank_90_single"
        required_score = 90
    allowed_operations = _allowed_operations(score)
    if action is DecisionAction.AUTOMATIC_KEEP and not agrees:
        # 原项目是有效基线时，证据不足的结果只是“不改”，不是取得了修改权限。
        allowed_operations = frozenset()
    return DecisionRoute(
        action,
        allowed_operations,
        required_score,
        review_policy=review_policy,
    )


def _review_action(materiality: MaterialityLevel) -> DecisionAction:
    if materiality is MaterialityLevel.M2:
        return DecisionAction.DOUBLE_AI_REVIEW
    if materiality is MaterialityLevel.M3:
        return DecisionAction.HUMAN_DECISION
    return DecisionAction.AI_REVIEW


def route_decision(
    score: int | None,
    original_state: OriginalItemState,
    materiality: MaterialityLevel,
    *,
    invalid_input: bool = False,
    cash_scope_confirmed: bool = True,
    company_rule_conflict: bool = False,
    vat_base_missing: bool = False,
    net_item_facts_missing: bool = False,
    new_reversal_pattern: bool = False,
    source_conflict: bool = False,
    business_conflict: bool = False,
    individual_tax_fact_missing: bool = False,
    direction_status: str = "compatible",
) -> DecisionRoute:
    if invalid_input:
        return DecisionRoute(
            DecisionAction.ISOLATE_INVALID_INPUT,
            frozenset(),
            forced_check="invalid_input",
        )
    if not cash_scope_confirmed:
        return DecisionRoute(
            DecisionAction.CONFIRM_CASH_SCOPE,
            frozenset(),
            forced_check="cash_scope",
        )
    if company_rule_conflict:
        return DecisionRoute(
            DecisionAction.HUMAN_DECISION,
            frozenset(),
            forced_check="company_rule_conflict",
        )
    has_valid_original = original_state in _VALID_ORIGINAL_STATES
    missing_fact_kind = (
        "vat_base_missing"
        if vat_base_missing
        else "net_item_facts_missing"
        if net_item_facts_missing
        else ""
    )
    if missing_fact_kind:
        if has_valid_original and materiality is not MaterialityLevel.M3:
            action = DecisionAction.AUTOMATIC_KEEP
        elif not has_valid_original and materiality is MaterialityLevel.M0:
            action = DecisionAction.LOW_AMOUNT_HUMAN_BATCH
        else:
            action = DecisionAction.HUMAN_DECISION
        return DecisionRoute(action, frozenset(), forced_check=missing_fact_kind)
    if individual_tax_fact_missing:
        if materiality is MaterialityLevel.M3:
            action = DecisionAction.HUMAN_DECISION
        elif materiality is MaterialityLevel.M2:
            action = DecisionAction.DOUBLE_AI_REVIEW
        elif has_valid_original:
            action = DecisionAction.AUTOMATIC_KEEP
        elif materiality is MaterialityLevel.M0:
            action = DecisionAction.LOW_AMOUNT_HUMAN_BATCH
        else:
            action = DecisionAction.HUMAN_BATCH
        return DecisionRoute(
            action,
            frozenset(),
            forced_check="individual_tax_service",
            review_policy=(
                "individual_tax_service"
                if action is DecisionAction.DOUBLE_AI_REVIEW
                else ""
            ),
        )
    if source_conflict and has_valid_original:
        if materiality is MaterialityLevel.M3:
            action = DecisionAction.HUMAN_DECISION
        elif materiality is MaterialityLevel.M2:
            action = DecisionAction.AI_REVIEW
        else:
            action = DecisionAction.AUTOMATIC_KEEP
        return DecisionRoute(
            action,
            frozenset(),
            70 if action is DecisionAction.AI_REVIEW else None,
            forced_check="source_conflict",
            review_policy=(
                "valid_original_retention"
                if action is DecisionAction.AI_REVIEW
                else ""
            ),
        )
    if source_conflict:
        action = (
            DecisionAction.AI_REVIEW
            if materiality is MaterialityLevel.M0
            else DecisionAction.DOUBLE_AI_REVIEW
            if materiality in {MaterialityLevel.M1, MaterialityLevel.M2}
            else DecisionAction.HUMAN_DECISION
        )
        return DecisionRoute(
            action,
            frozenset(),
            forced_check="source_conflict",
            review_policy=(
                "blank_low_single"
                if materiality is MaterialityLevel.M0
                else "blank_low_majority"
                if materiality in {MaterialityLevel.M1, MaterialityLevel.M2}
                else ""
            ),
        )
    if business_conflict and has_valid_original:
        action = (
            DecisionAction.HUMAN_DECISION
            if materiality is MaterialityLevel.M3
            else DecisionAction.AUTOMATIC_KEEP
        )
        return DecisionRoute(action, frozenset(), forced_check="business_conflict")
    if business_conflict:
        action = (
            DecisionAction.LOW_AMOUNT_HUMAN_BATCH
            if materiality is MaterialityLevel.M0
            else DecisionAction.HUMAN_DECISION
        )
        return DecisionRoute(action, frozenset(), forced_check="business_conflict")
    if direction_status == "incompatible" and new_reversal_pattern:
        action = (
            DecisionAction.CONFIRM_REVERSAL_RULE
            if materiality is MaterialityLevel.M3
            else _review_action(materiality)
        )
        return DecisionRoute(
            action,
            frozenset(),
            forced_check="new_reversal",
            review_policy=(
                "reversal_one_time"
                if action in {DecisionAction.AI_REVIEW, DecisionAction.DOUBLE_AI_REVIEW}
                else ""
            ),
        )
    if direction_status == "incompatible":
        action = _review_action(materiality)
        return DecisionRoute(
            action,
            frozenset(),
            forced_check="direction",
            review_policy=(
                "direction_compatibility"
                if action in {DecisionAction.AI_REVIEW, DecisionAction.DOUBLE_AI_REVIEW}
                else ""
            ),
        )
    if direction_status not in {"compatible", "approved_reversal"}:
        raise ValueError(f"不允许的现金方向状态：{direction_status}")
    if score is None:
        raise ValueError("无可用证据分数时必须给出来源冲突或其他强制检查")
    return route_normal_decision(score, original_state, materiality)
