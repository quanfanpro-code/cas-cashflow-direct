# -*- coding: utf-8 -*-
"""证据打分模块。

口径（2026-08-19 独立复核修复）：
- 初始矩阵：摘要 40 分；通用对方科目明细 30 分；仅对方科目一级 15 分；
  经确认的公司专属规则（词典 custom 层）40 分。
- 同一来源只计一次：摘要命中多个重复、近义或包含关系词仍只计一份摘要证据；
  同一科目的一级与明细只取较强的一层；科目侧多段命中同一项目只取最强一段。
- 项目总分 = 该项目最高摘要分 + 最高对方科目分（摘要 40 + 明细 30 = 70；
  摘要 + 一级 = 55）。原标签一致、现金方向、规则编号、同一科目不同层级
  均不计为独立来源，也不加分。
- 冲突判定：另一项目总分 ≥40 且与首选分差小于 30（一个完整明细档）才算冲突；
  冲突或现金方向不兼容时不得自动收口，不能仅靠扣分消除红线。
- 推翻原表门槛：总分 ≥70、摘要与对方科目两个独立来源共同支持、无冲突且
  现金方向兼容，四者缺一不可。
- 70/40 是内部业务证据指数，只用于控制复核深度与改判权限，不代表正确率，
  不是准则规定，也未做统计校准。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from cashflow_direct.models import CashflowComponent

SOURCE_SUMMARY = "summary"
SOURCE_ACCOUNT_DETAIL = "account_detail"
SOURCE_ACCOUNT_LEVEL1 = "account_level1"

HIGH_THRESHOLD = 70
MEDIUM_THRESHOLD = 40

_BASE_SCORE = {
    SOURCE_SUMMARY: 40,
    SOURCE_ACCOUNT_DETAIL: 30,
    SOURCE_ACCOUNT_LEVEL1: 15,
}
_CONFLICT_MIN_TOTAL = 40
_CONFLICT_MARGIN = 30
_OVERRIDE_MIN_SCORE = 70

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


@dataclass(frozen=True, slots=True)
class EvidenceScore:
    item_id: str
    total: int
    tier: str
    sources: tuple[str, ...]
    conflict: bool
    conflict_item_ids: tuple[str, ...]
    can_override_label: bool
    rule_scores: tuple[RuleScore, ...]


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


def score_rule(
    rule,
    component: CashflowComponent,
    item_normal_direction: str,
) -> RuleScore | None:
    """给一条已命中规则打分：摘要与对方科目两渠道分别计分，同一来源只计一次。"""
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
    account_detail_hits = tuple(
        term for term in rule.account_terms if any(term in segment for segment in detail_segments)
    )
    # 同一科目的一级与明细只取较强一层：存在明细命中时一级命中不再计
    account_level1_hits = (
        ()
        if account_detail_hits
        else tuple(
            term
            for term in rule.account_terms
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
    factor = 1.0 if compatible else 0.5
    summary_part = round(_BASE_SCORE[SOURCE_SUMMARY] * factor) if summary_hits else 0
    if account_detail_hits:
        account_channel = SOURCE_ACCOUNT_DETAIL
    elif account_level1_hits:
        account_channel = SOURCE_ACCOUNT_LEVEL1
    else:
        account_channel = ""
    account_part = round(_BASE_SCORE[account_channel] * factor) if account_channel else 0
    channels = tuple(
        channel
        for channel, part in (
            (SOURCE_SUMMARY, summary_part),
            (account_channel, account_part),
        )
        if part
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
    )


def aggregate_evidence(rule_scores) -> EvidenceScore | None:
    """汇总全部命中规则的得分，产出唯一首选项目与推翻原表门槛判断。"""
    scores = tuple(score for score in rule_scores if score is not None)
    if not scores:
        return None
    # 每个项目：摘要分与科目分分别取最高，来源只计一次
    items: dict[str, dict[str, object]] = {}
    for score in scores:
        slot = items.setdefault(
            score.item_id,
            {
                "summary": 0,
                "account": 0,
                "summary_ok": True,
                "account_ok": True,
                "account_channel": "",
                "best": None,
            },
        )
        if score.summary_part > slot["summary"]:
            slot["summary"] = score.summary_part
            slot["summary_ok"] = score.direction_compatible
        if score.account_part > slot["account"]:
            slot["account"] = score.account_part
            slot["account_ok"] = score.direction_compatible
            slot["account_channel"] = next(
                (
                    channel
                    for channel in score.channels
                    if channel in (SOURCE_ACCOUNT_DETAIL, SOURCE_ACCOUNT_LEVEL1)
                ),
                SOURCE_ACCOUNT_DETAIL,
            )
        best = slot["best"]
        if best is None or (-score.score, score.priority, score.rule_id) < (
            -best.score,
            best.priority,
            best.rule_id,
        ):
            slot["best"] = score

    def _total(item_id: str) -> int:
        slot = items[item_id]
        return slot["summary"] + slot["account"]

    item_id = min(
        items,
        key=lambda candidate: (
            -_total(candidate),
            items[candidate]["best"].priority,
            items[candidate]["best"].rule_id,
        ),
    )
    best_slot = items[item_id]
    best_total = _total(item_id)
    # 冲突：另一项目总分 ≥40 且与首选分差小于一个完整明细档（30 分）
    conflict_item_ids = tuple(
        sorted(
            other_id
            for other_id in items
            if other_id != item_id
            and _total(other_id) >= _CONFLICT_MIN_TOTAL
            and best_total - _total(other_id) < _CONFLICT_MARGIN
        )
    )
    conflict = bool(conflict_item_ids)
    sources = tuple(
        channel
        for channel, part in (
            (SOURCE_SUMMARY, best_slot["summary"]),
            (best_slot["account_channel"], best_slot["account"]),
        )
        if part
    )
    total = min(100, best_total)
    tier = "high" if total >= HIGH_THRESHOLD else "medium" if total >= MEDIUM_THRESHOLD else "low"
    return EvidenceScore(
        item_id=item_id,
        total=total,
        tier=tier,
        sources=sources,
        conflict=conflict,
        conflict_item_ids=conflict_item_ids,
        can_override_label=(
            best_total >= _OVERRIDE_MIN_SCORE
            and best_slot["summary"] > 0
            and best_slot["account"] > 0
            and not conflict
            and best_slot["summary_ok"]
            and best_slot["account_ok"]
        ),
        rule_scores=scores,
    )
