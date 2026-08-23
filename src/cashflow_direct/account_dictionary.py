# -*- coding: utf-8 -*-
"""科目语义词典模块（Task 4）。

把对方科目（含二三级明细）映射为业务语义标签与疑似现流项目的词典。
内置通用层是基线；普通运行时解释只补齐未知路径；只有用户确认且带NOTE编号的条目属于企业专属层。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.decision_policy import EvidenceQuality
from cashflow_direct.evidence import (
    SOURCE_ACCOUNT_PATH,
    SOURCE_SUMMARY,
    RuleScore,
    split_account_levels,
)
from cashflow_direct.models import CashflowComponent


@dataclass(frozen=True, slots=True)
class AccountSemanticEntry:
    account: str
    semantic: str
    item_id: str
    basis: str
    confidence: str
    layer: str
    note_id: str = ""
    inflow_item_id: str = ""
    outflow_item_id: str = ""
    classification_facts: tuple[str, ...] = ()
    candidate_item_ids: tuple[str, ...] = ()
    inflow_candidate_item_ids: tuple[str, ...] = ()
    outflow_candidate_item_ids: tuple[str, ...] = ()

    def item_for_cash_direction(self, cash_delta_cent: int) -> str:
        """同一路径允许按现金流入、流出分别指向项目。"""
        if cash_delta_cent > 0 and self.inflow_item_id:
            return self.inflow_item_id
        if cash_delta_cent < 0 and self.outflow_item_id:
            return self.outflow_item_id
        return self.item_id

    def candidates_for_cash_direction(self, cash_delta_cent: int) -> tuple[str, ...]:
        if cash_delta_cent > 0 and self.inflow_candidate_item_ids:
            return self.inflow_candidate_item_ids
        if cash_delta_cent < 0 and self.outflow_candidate_item_ids:
            return self.outflow_candidate_item_ids
        if self.candidate_item_ids:
            return self.candidate_item_ids
        item_id = self.item_for_cash_direction(cash_delta_cent)
        return (item_id,) if item_id else ()


@dataclass(frozen=True, slots=True)
class AccountDictionary:
    entries: tuple[AccountSemanticEntry, ...]

    def lookup(self, segment: str) -> AccountSemanticEntry | None:
        """用户确认的企业特殊规则优先，普通运行时补充最后使用。"""
        for layer in ("custom", "common", "runtime"):
            hit = next(
                (entry for entry in self.entries if entry.account == segment and entry.layer == layer),
                None,
            )
            if hit is not None:
                return hit
        return None

    def lookup_path(self, account_path: str) -> AccountSemanticEntry | None:
        """企业特殊规则可覆盖基线；普通运行时解释不能覆盖内置通用语义。"""
        for layer in ("custom", "common"):
            hit = next(
                (
                    entry
                    for entry in self.entries
                    if entry.account == account_path and entry.layer == layer
                ),
                None,
            )
            if hit is not None:
                return hit
        segments = tuple(reversed(split_account_levels(account_path)))
        for layer in ("custom", "common"):
            for segment in segments:
                hit = next(
                    (
                        entry
                        for entry in self.entries
                        if entry.account == segment and entry.layer == layer
                    ),
                    None,
                )
                if hit is not None:
                    return hit
        for entry in self.entries:
            if entry.account == account_path and entry.layer == "runtime":
                return entry
        for segment in segments:
            hit = next(
                (
                    entry
                    for entry in self.entries
                    if entry.account == segment and entry.layer == "runtime"
                ),
                None,
            )
            if hit is not None:
                return hit
        return None


@dataclass(frozen=True, slots=True)
class SummarySemanticEntry:
    summary: str
    semantic: str
    item_id: str
    basis: str
    confidence: str
    classification_facts: tuple[str, ...]
    candidate_item_ids: tuple[str, ...] = ()
    negation: tuple[str, ...] = ()
    uncertainty: tuple[str, ...] = ()
    conditionality: tuple[str, ...] = ()
    source_spans: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SummaryDictionary:
    entries: tuple[SummarySemanticEntry, ...]

    def lookup(self, summary: str) -> SummarySemanticEntry | None:
        return next((entry for entry in self.entries if entry.summary == summary), None)


def _from_payload(payload: dict, default_layer: str) -> AccountSemanticEntry:
    return AccountSemanticEntry(
        account=str(payload["account"]),
        semantic=str(payload.get("semantic", "")),
        item_id=str(payload.get("item_id", "")),
        basis=str(payload.get("basis", "")),
        confidence=str(payload.get("confidence", "low")),
        layer=str(payload.get("layer", default_layer)),
        note_id=str(payload.get("note_id", "")),
        inflow_item_id=str(payload.get("inflow_item_id", "")),
        outflow_item_id=str(payload.get("outflow_item_id", "")),
        classification_facts=tuple(
            str(value) for value in payload.get("classification_facts", ())
        ),
        candidate_item_ids=tuple(
            str(value) for value in payload.get("candidate_item_ids", ())
        ),
        inflow_candidate_item_ids=tuple(
            str(value) for value in payload.get("inflow_candidate_item_ids", ())
        ),
        outflow_candidate_item_ids=tuple(
            str(value) for value in payload.get("outflow_candidate_item_ids", ())
        ),
    )


def load_common_dictionary(root: Path) -> AccountDictionary:
    path = Path(root) / "references" / "科目语义词典.json"
    if not path.is_file():
        return AccountDictionary(())
    with path.open("r", encoding="utf-8-sig") as source:
        payload = json.load(source)
    return AccountDictionary(
        tuple(_from_payload(item, "common") for item in payload.get("entries", ()))
    )


def merge_dictionaries(common: AccountDictionary, custom: AccountDictionary) -> AccountDictionary:
    return AccountDictionary(tuple(common.entries) + tuple(custom.entries))


def collect_detail_segments(account_names) -> tuple[str, ...]:
    """提取全部科目名的二三级明细段，去重排序；一级段不返回。"""
    return tuple(
        sorted(
            {
                segment
                for name in account_names
                for segment in split_account_levels(str(name))[1:]
            }
        )
    )


def refuses_general_semantic_judgment(
    semantic: str, basis: str, item_id: str
) -> bool:
    """识别把“无公司特殊规则”误当成“不判断通用语义”的无效答复。"""
    if item_id:
        return False
    text = f"{semantic}|{basis}"
    refusal_terms = (
        "无企业专属补充语义",
        "不新增企业专属语义",
        "没有公司特殊规则",
        "无公司特殊规则",
    )
    return any(term in text for term in refusal_terms)


def path_basis_is_traceable(account_path: str, basis: str) -> bool:
    """完整路径本身就是本行路径来源，允许作为科目语义判断的可追查依据。"""
    text = (basis or "").strip()
    return bool(
        account_path
        and account_path in text
        and ("完整路径" in text or "科目路径" in text)
    )


def score_dictionary_hits(component: CashflowComponent, dictionary: AccountDictionary) -> tuple[RuleScore, ...]:
    """词典层级只决定适用顺序；证据质量统一使用0、10、25、45。"""
    scores: list[RuleScore] = []
    seen: set[tuple[str, str]] = set()
    for account in component.counterpart_accounts:
        entry = dictionary.lookup_path(account)
        if entry is None:
            continue
        candidate_item_ids = tuple(
            dict.fromkeys(entry.candidates_for_cash_direction(component.cash_delta_cent))
        )
        if not candidate_item_ids:
            continue
        key = (account, "|".join(candidate_item_ids))
        if key in seen:
            continue
        seen.add(key)
        quality = {
            "high": EvidenceQuality.STRONG,
            "medium": EvidenceQuality.MEDIUM,
            "low": EvidenceQuality.WEAK,
        }.get(entry.confidence, EvidenceQuality.INVALID)
        if quality is EvidenceQuality.INVALID:
            continue
        scores.append(
            RuleScore(
                rule_id=f"DICT-{entry.layer.upper()}-{account}",
                item_id=candidate_item_ids[0] if len(candidate_item_ids) == 1 else "",
                priority=50,
                source=SOURCE_ACCOUNT_PATH,
                score=quality.value,
                summary_part=0,
                account_part=quality.value,
                direction_compatible=True,
                summary_hits=(),
                account_hits=(account,),
                channels=(SOURCE_ACCOUNT_PATH,),
                note_id=entry.note_id,
                account_facts=(
                    entry.classification_facts
                    or (
                        f"business:{'|'.join(candidate_item_ids)}",
                        f"account_context:{account}",
                    )
                ),
                business_object=entry.semantic,
                purpose=(
                    split_account_levels(account)[-1]
                    if split_account_levels(account)
                    else ""
                ),
                candidate_item_ids=candidate_item_ids,
            )
        )
    return tuple(scores)


def score_summary_hit(
    component: CashflowComponent,
    dictionary: SummaryDictionary,
) -> RuleScore | None:
    """摘要只使用已结构化确认的完整语义，不在这里做关键词分类。"""
    entry = dictionary.lookup(component.summary)
    candidate_item_ids = tuple(
        dict.fromkeys(
            entry.candidate_item_ids
            or ((entry.item_id,) if entry.item_id else ())
        )
    ) if entry is not None else ()
    if entry is None or not candidate_item_ids or not entry.classification_facts:
        return None
    quality = {
        "high": EvidenceQuality.STRONG,
        "medium": EvidenceQuality.MEDIUM,
        "low": EvidenceQuality.WEAK,
    }.get(entry.confidence, EvidenceQuality.INVALID)
    if quality is EvidenceQuality.INVALID:
        return None
    return RuleScore(
        rule_id=f"SUMMARY-SEMANTIC-{component.component_id}",
        item_id=candidate_item_ids[0] if len(candidate_item_ids) == 1 else "",
        priority=-100,
        source=SOURCE_SUMMARY,
        score=quality.value,
        summary_part=quality.value,
        account_part=0,
        direction_compatible=True,
        summary_hits=(component.summary,),
        account_hits=(),
        channels=(SOURCE_SUMMARY,),
        summary_facts=entry.classification_facts,
        business_object=entry.semantic,
        candidate_item_ids=candidate_item_ids,
    )
