# -*- coding: utf-8 -*-
"""合并摘要语义与完整对方科目路径的独立证据。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from cashflow_direct.decision_policy import (
    EvidenceQuality,
    EvidenceSource,
    EvidenceSourceAssessment,
    combine_source_assessments,
)

SOURCE_SUMMARY = "summary"
SOURCE_ACCOUNT_PATH = "account_path"

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
