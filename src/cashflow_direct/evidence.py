# -*- coding: utf-8 -*-
"""把摘要和完整对方科目路径转换为四档质量与十种离散分数。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from cashflow_direct.decision_policy import (
    EvidenceQuality,
    EvidenceSource,
    EvidenceSourceAssessment,
    combine_source_assessments,
)
from cashflow_direct.models import CashflowComponent

SOURCE_SUMMARY = "summary"
SOURCE_ACCOUNT_PATH = "account_path"

_QUALITY_BY_LEVEL = {
    "low": EvidenceQuality.WEAK,
    "medium": EvidenceQuality.MEDIUM,
    "high": EvidenceQuality.STRONG,
}

# 层级分隔符：下划线、斜杠、反斜杠、大于号、竖线、中英文冒号，以及两侧有空格的横线。
# 名称内部的普通横线（两侧无空格，如"财务费用-利息收入"）不拆分。
_LEVEL_SEPARATOR_RE = re.compile(r"[/_\\>|：:]|\s+[-－—–]\s+")


@dataclass(frozen=True, slots=True)
class RuleScore:
    rule_id: str
    item_id: str
    priority: int
    source: str
    score: int
    summary_part: int
    account_part: int
    direction_compatible: bool
    summary_hits: tuple[str, ...]
    account_hits: tuple[str, ...]
    channels: tuple[str, ...]
    note_id: str = ""
    summary_facts: tuple[str, ...] = ()
    account_facts: tuple[str, ...] = ()
    business_object: str = ""
    purpose: str = ""
    candidate_item_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceScore:
    item_id: str
    score: int | None
    tier: str
    sources: tuple[str, ...]
    conflict: bool
    conflict_item_ids: tuple[str, ...]
    rule_scores: tuple[RuleScore, ...]
    summary_quality: int = 0
    account_path_quality: int = 0
    sources_independent: bool = False
    candidate_item_ids: tuple[str, ...] = ()
    summary_candidate_item_ids: tuple[str, ...] = ()
    account_path_candidate_item_ids: tuple[str, ...] = ()

def split_account_levels(account_name: str) -> tuple[str, ...]:
    """把科目名按常见层级分隔符拆成层级段，空段丢弃。"""
    return tuple(
        segment.strip()
        for segment in _LEVEL_SEPARATOR_RE.split(account_name)
        if segment.strip()
    )


def _dedupe_contained(terms: tuple[str, ...]) -> tuple[str, ...]:
    """去掉被其他命中词包含的短词：重复、近义或包含关系的词只留最长者。"""
    return tuple(
        term
        for term in terms
        if not any(term != other and term in other for other in terms)
    )


def _semantic_text(value: str) -> str:
    text = re.sub(r"[\s，。；：、,.!！?？（）()《》\[\]【】/_\\>|-]+", "", value)
    for generic in ("支付", "收到", "收取", "收款", "付款", "转账"):
        text = text.replace(generic, "")
    return text


def score_rule(
    rule,
    component: CashflowComponent,
    item_normal_direction: str,
) -> RuleScore | None:
    """给一条已命中规则评四档质量；方向只作强制检查，不再折半扣分。"""
    summary_hits = _dedupe_contained(
        tuple(
            dict.fromkeys(
                term
                for term in (*rule.summary_terms, *rule.account_terms)
                if term in component.summary
            )
        )
    )
    level1_segments: list[str] = []
    detail_segments: list[str] = []
    for account in component.counterpart_accounts:
        segments = split_account_levels(account)
        if not segments:
            continue
        level1_segments.append(segments[0])
        detail_segments.extend(segments[1:])
    account_terms = tuple(
        dict.fromkeys((*rule.account_terms, *rule.sole_account_terms))
    )
    account_detail_hits = tuple(
        term for term in account_terms if any(term in segment for segment in detail_segments)
    )
    # 同一科目的一级与明细只取较强一层：存在明细命中时一级命中不再计
    account_level1_hits = (
        ()
        if account_detail_hits
        else tuple(
            term
            for term in account_terms
            if any(term in segment for segment in level1_segments)
        )
    )
    # require_account 规则（如职工薪酬分流）必须命中对方科目才作数
    if rule.require_account and not (
        account_detail_hits or account_level1_hits
    ):
        return None
    if not summary_hits and not (account_detail_hits or account_level1_hits):
        return None
    direction = "inflow" if component.cash_delta_cent > 0 else "outflow"
    compatible = direction == item_normal_direction
    quality = _QUALITY_BY_LEVEL.get(rule.evidence_level, EvidenceQuality.WEAK)
    summary_part = quality.value if summary_hits else 0
    if account_detail_hits:
        account_part = quality.value
    elif account_level1_hits:
        account_part = min(quality.value, EvidenceQuality.MEDIUM.value)
    else:
        account_part = 0
    channels = tuple(
        channel
        for channel, part in (
            (SOURCE_SUMMARY, summary_part),
            (SOURCE_ACCOUNT_PATH, account_part),
        )
        if part
    )
    business_fact = f"business:{rule.item_id}"
    account_context = "|".join(
        sorted(_semantic_text(account) for account in component.counterpart_accounts)
    )
    summary_text = _semantic_text(component.summary)
    path_leaf_texts = {
        _semantic_text(segment)
        for account in component.counterpart_accounts
        for segment in split_account_levels(account)
    }
    summary_adds_context = bool(
        summary_part
        and summary_text
        and summary_text not in path_leaf_texts
        and summary_text
        not in {
            _semantic_text(term)
            for term in (*account_detail_hits, *account_level1_hits)
        }
    )
    summary_facts = (
        (business_fact, f"summary_context:{summary_text}")
        if summary_adds_context
        else ((business_fact,) if summary_part else ())
    )
    account_facts = (
        (business_fact, f"account_context:{account_context}")
        if account_part and (not summary_part or summary_adds_context)
        else ((business_fact,) if account_part else ())
    )
    return RuleScore(
        rule_id=rule.rule_id,
        item_id=rule.item_id,
        priority=rule.priority,
        source=channels[0],
        score=summary_part + account_part,
        summary_part=summary_part,
        account_part=account_part,
        direction_compatible=compatible,
        summary_hits=summary_hits,
        account_hits=(*account_detail_hits, *account_level1_hits),
        channels=channels,
        candidate_item_ids=rule.candidate_item_ids,
        summary_facts=summary_facts,
        account_facts=account_facts,
    )


def aggregate_evidence(
    rule_scores,
    *,
    sources_independent: bool | None = None,
) -> EvidenceScore | None:
    """每个来源只选一个最强解释，再由统一评分函数合计。"""
    scores = tuple(score for score in rule_scores if score is not None)
    if not scores:
        return None
    summary_score = min(
        (score for score in scores if score.summary_part),
        key=lambda item: (-item.summary_part, item.priority, item.rule_id),
        default=None,
    )
    account_score = min(
        (score for score in scores if score.account_part),
        key=lambda item: (-item.account_part, item.priority, item.rule_id),
        default=None,
    )
    summary = EvidenceSourceAssessment(
        EvidenceSource.SUMMARY,
        "" if summary_score is None else summary_score.item_id,
        EvidenceQuality.INVALID if summary_score is None else EvidenceQuality(summary_score.summary_part),
        "" if summary_score is None else "、".join(summary_score.summary_hits),
        () if summary_score is None else summary_score.summary_facts,
        ()
        if summary_score is None
        else summary_score.candidate_item_ids or (summary_score.item_id,),
    )
    account = EvidenceSourceAssessment(
        EvidenceSource.ACCOUNT_PATH,
        "" if account_score is None else account_score.item_id,
        EvidenceQuality.INVALID if account_score is None else EvidenceQuality(account_score.account_part),
        "" if account_score is None else "、".join(account_score.account_hits),
        () if account_score is None else account_score.account_facts,
        ()
        if account_score is None
        else account_score.candidate_item_ids or (account_score.item_id,),
    )
    combined = combine_source_assessments(
        summary,
        account,
        sources_independent=sources_independent,
    )
    used_scores = tuple(score for score in (summary_score, account_score) if score is not None)
    primary = min(
        used_scores,
        key=lambda item: (-max(item.summary_part, item.account_part), item.priority, item.rule_id),
    )
    if combined.conflict:
        sources = (SOURCE_SUMMARY, SOURCE_ACCOUNT_PATH)
    elif combined.independent_source_count == 2:
        sources = (SOURCE_SUMMARY, SOURCE_ACCOUNT_PATH)
    elif summary_score is not None and (
        account_score is None or summary_score.summary_part > account_score.account_part
    ):
        sources = (SOURCE_SUMMARY,)
    elif account_score is not None:
        sources = (SOURCE_ACCOUNT_PATH,)
    else:
        sources = ()
    score = combined.score
    tier = (
        "conflict"
        if score is None
        else "high" if score >= 70 else "medium" if score >= 45 else "low"
    )
    return EvidenceScore(
        item_id=(
            primary.item_id
            or next(iter(combined.conflict_item_ids), "")
            if combined.conflict
            else combined.candidate_item_id
        ),
        score=score,
        tier=tier,
        sources=sources,
        conflict=combined.conflict,
        conflict_item_ids=combined.conflict_item_ids,
        rule_scores=scores,
        summary_quality=summary.quality.value,
        account_path_quality=account.quality.value,
        sources_independent=combined.sources_independent,
        candidate_item_ids=combined.candidate_item_ids,
        summary_candidate_item_ids=summary.candidate_item_ids,
        account_path_candidate_item_ids=account.candidate_item_ids,
    )
