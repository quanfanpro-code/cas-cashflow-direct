from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum

from cashflow_direct.rule_registry import default_rule_registry


_RULE_REGISTRY = default_rule_registry()


class EvidenceQuality(IntEnum):
    INVALID = int(_RULE_REGISTRY.evidence_policy["evidence_qualities"]["invalid"])
    WEAK = int(_RULE_REGISTRY.evidence_policy["evidence_qualities"]["weak"])
    MEDIUM = int(_RULE_REGISTRY.evidence_policy["evidence_qualities"]["medium"])
    STRONG = int(_RULE_REGISTRY.evidence_policy["evidence_qualities"]["strong"])


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
    HUMAN_DECISION = "human_decision"
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

    def __post_init__(self) -> None:
        if self.score in {70, 90} and not (
            self.independent_source_count == 2 and self.sources_independent
        ):
            raise ValueError("70分或90分必须来自两个独立来源")


@dataclass(frozen=True, slots=True)
class DecisionRoute:
    action: DecisionAction
    allowed_operations: frozenset[DecisionOperation]
    forced_check: str = ""
    review_policy: str = ""
    rule_id: str = ""


_GENERIC_FACT_PREFIXES = ("cash_direction:",)
_ALLOWED_SCORES = frozenset(_RULE_REGISTRY.allowed_scores)
AUTOMATIC_CHANGE_SCORE_OPTIONS = _RULE_REGISTRY.automatic_change_score_options
DEFAULT_AUTOMATIC_CHANGE_SCORE = _RULE_REGISTRY.default_automatic_change_score

_VALID_ORIGINAL_STATES = frozenset(
    {
        OriginalItemState.AGREES,
        OriginalItemState.CONFLICTS,
        OriginalItemState.PENDING_COMPARISON,
    }
)


def _original_group(original_state: OriginalItemState) -> str:
    if original_state is OriginalItemState.AGREES:
        return "agrees"
    if original_state in _VALID_ORIGINAL_STATES:
        return "valid_original"
    return "blank"


def _route_from_cell(
    cell: dict[str, str],
    *,
    forced_check: str = "",
    allowed_operations: frozenset[DecisionOperation] = frozenset(),
) -> DecisionRoute:
    return DecisionRoute(
        DecisionAction(cell["action"]),
        allowed_operations,
        forced_check=forced_check,
        review_policy=cell["review_policy"],
        rule_id=cell["rule_id"],
    )


def unresolved_after_ai_review_action() -> DecisionAction:
    """人工智能复核仍无法形成唯一结果时的唯一出口。"""
    return DecisionAction(
        _RULE_REGISTRY.evidence_policy["ai_review_outcomes"]["unresolved_action"]
    )


def validate_automatic_change_threshold(value: int) -> int:
    if value not in AUTOMATIC_CHANGE_SCORE_OPTIONS:
        raise ValueError("自动修改最低证据分只允许45、50、55、70、90")
    return value


def change_is_authorized(
    score: int,
    threshold: int,
    summary_quality: int = 0,
    account_path_quality: int = 0,
) -> bool:
    threshold = validate_automatic_change_threshold(threshold)
    return score >= threshold


def score_meets_change_threshold(
    score: int,
    threshold: int,
    summary_quality: int = 0,
    account_path_quality: int = 0,
) -> bool:
    return change_is_authorized(
        score,
        threshold,
        summary_quality,
        account_path_quality,
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
    return bool(
        summary_facts
        and path_facts
        and ((summary_facts - path_facts) or (path_facts - summary_facts))
    )


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


def _allowed_operations(
    score: int,
    automatic_change_threshold: int = DEFAULT_AUTOMATIC_CHANGE_SCORE,
    *,
    valid_original: bool = False,
    summary_quality: int = 0,
    account_path_quality: int = 0,
) -> frozenset[DecisionOperation]:
    if valid_original and score_meets_change_threshold(
        score,
        automatic_change_threshold,
        summary_quality,
        account_path_quality,
    ):
        return frozenset(DecisionOperation)
    if score in AUTOMATIC_CHANGE_SCORE_OPTIONS and score < DEFAULT_AUTOMATIC_CHANGE_SCORE:
        return frozenset({DecisionOperation.KEEP})
    if score in AUTOMATIC_CHANGE_SCORE_OPTIONS:
        return frozenset(DecisionOperation)
    return frozenset()


def route_normal_decision(
    score: int,
    original_state: OriginalItemState,
    materiality: MaterialityLevel,
    automatic_change_threshold: int = DEFAULT_AUTOMATIC_CHANGE_SCORE,
    *,
    summary_quality: int = 0,
    account_path_quality: int = 0,
) -> DecisionRoute:
    automatic_change_threshold = validate_automatic_change_threshold(
        automatic_change_threshold
    )
    if score not in _ALLOWED_SCORES:
        raise ValueError(f"不允许的证据分数：{score}")

    agrees = original_state is OriginalItemState.AGREES
    cell = _RULE_REGISTRY.normal_action_cell(
        _original_group(original_state), score, materiality.value
    )
    action = DecisionAction(cell["action"])
    if not agrees and original_state in _VALID_ORIGINAL_STATES:
        threshold_met = score_meets_change_threshold(
            score,
            automatic_change_threshold,
            summary_quality,
            account_path_quality,
        )
        if materiality is MaterialityLevel.M0:
            action = (
                DecisionAction.AUTOMATIC_CHANGE
                if threshold_met
                else DecisionAction.AUTOMATIC_KEEP
            )
        elif materiality is MaterialityLevel.M1:
            action = (
                DecisionAction.AI_REVIEW
                if threshold_met
                else DecisionAction.AUTOMATIC_KEEP
            )
    if action is DecisionAction.AUTOMATIC_FILL and original_state is OriginalItemState.CONFLICTS:
        action = DecisionAction.AUTOMATIC_CHANGE

    review_policy = cell["review_policy"]
    if not agrees and original_state in _VALID_ORIGINAL_STATES:
        if materiality is MaterialityLevel.M0:
            review_policy = ""
        elif materiality is MaterialityLevel.M1:
            review_policy = (
                "valid_original_change"
                if action is DecisionAction.AI_REVIEW
                else ""
            )
    allowed_operations = _allowed_operations(
        score,
        automatic_change_threshold,
        valid_original=(
            not agrees and original_state in _VALID_ORIGINAL_STATES
        ),
        summary_quality=summary_quality,
        account_path_quality=account_path_quality,
    )
    if action is DecisionAction.AUTOMATIC_KEEP and not agrees:
        # 原项目是有效基线时，证据不足的结果只是“不改”，不是取得了修改权限。
        allowed_operations = frozenset()
    return DecisionRoute(
        action,
        allowed_operations,
        review_policy=review_policy,
        rule_id=cell["rule_id"],
    )


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
    source_conflict: bool = False,
    business_conflict: bool = False,
    individual_tax_fact_missing: bool = False,
    direction_status: str = "compatible",
    automatic_change_threshold: int = DEFAULT_AUTOMATIC_CHANGE_SCORE,
    summary_quality: int = 0,
    account_path_quality: int = 0,
) -> DecisionRoute:
    automatic_change_threshold = validate_automatic_change_threshold(
        automatic_change_threshold
    )
    has_valid_original = original_state in _VALID_ORIGINAL_STATES
    forced_original_group = "valid_original" if has_valid_original else "blank"
    if invalid_input:
        return _route_from_cell(
            _RULE_REGISTRY.forced_route_cell(
                "invalid_input", forced_original_group, materiality.value
            ),
            forced_check="invalid_input",
        )
    if not cash_scope_confirmed:
        return _route_from_cell(
            _RULE_REGISTRY.forced_route_cell(
                "cash_scope", forced_original_group, materiality.value
            ),
            forced_check="cash_scope",
        )
    if company_rule_conflict:
        return _route_from_cell(
            _RULE_REGISTRY.forced_route_cell(
                "company_rule_conflict", forced_original_group, materiality.value
            ),
            forced_check="company_rule_conflict",
        )
    missing_fact_kind = (
        "vat_base_missing"
        if vat_base_missing
        else "net_item_facts_missing"
        if net_item_facts_missing
        else ""
    )
    if missing_fact_kind:
        return _route_from_cell(
            _RULE_REGISTRY.forced_route_cell(
                "missing_net_or_vat", forced_original_group, materiality.value
            ),
            forced_check=missing_fact_kind,
        )
    if individual_tax_fact_missing:
        return _route_from_cell(
            _RULE_REGISTRY.forced_route_cell(
                "individual_tax_service", forced_original_group, materiality.value
            ),
            forced_check="individual_tax_service",
        )
    if source_conflict:
        return _route_from_cell(
            _RULE_REGISTRY.forced_route_cell(
                "source_conflict", forced_original_group, materiality.value
            ),
            forced_check="source_conflict",
        )
    if business_conflict:
        return _route_from_cell(
            _RULE_REGISTRY.forced_route_cell(
                "business_conflict", forced_original_group, materiality.value
            ),
            forced_check="business_conflict",
        )
    if direction_status not in {"compatible", "incompatible"}:
        raise ValueError(f"不允许的现金方向状态：{direction_status}")
    if score is None:
        raise ValueError("无可用证据分数时必须给出来源冲突或其他强制检查")
    return route_normal_decision(
        score,
        original_state,
        materiality,
        automatic_change_threshold,
        summary_quality=summary_quality,
        account_path_quality=account_path_quality,
    )
