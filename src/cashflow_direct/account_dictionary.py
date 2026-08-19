# -*- coding: utf-8 -*-
"""科目语义词典模块（Task 4）。

把对方科目（含二三级明细）映射为业务语义标签与疑似现流项目的词典，
分通用层（内置 static）与企业专属层（运行时 AI 生成）。专属命中权重高于通用命中。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.evidence import SOURCE_ACCOUNT_DETAIL, RuleScore, split_account_levels
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


@dataclass(frozen=True, slots=True)
class AccountDictionary:
    entries: tuple[AccountSemanticEntry, ...]

    def lookup(self, segment: str) -> AccountSemanticEntry | None:
        """custom 层优先于 common 层。"""
        for layer in ("custom", "common"):
            hit = next(
                (entry for entry in self.entries if entry.account == segment and entry.layer == layer),
                None,
            )
            if hit is not None:
                return hit
        return None


def _from_payload(payload: dict, default_layer: str) -> AccountSemanticEntry:
    return AccountSemanticEntry(
        account=str(payload["account"]),
        semantic=str(payload.get("semantic", "")),
        item_id=str(payload.get("item_id", "")),
        basis=str(payload.get("basis", "")),
        confidence=str(payload.get("confidence", "low")),
        layer=str(payload.get("layer", default_layer)),
        note_id=str(payload.get("note_id", "")),
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


def score_dictionary_hits(component: CashflowComponent, dictionary: AccountDictionary) -> tuple[RuleScore, ...]:
    """词典命中折算为对方科目明细层分数：custom 层 40（medium 30）、common 层 30（medium 20）。"""
    scores: list[RuleScore] = []
    seen: set[tuple[str, str]] = set()
    for account in component.counterpart_accounts:
        for segment in split_account_levels(account)[1:]:
            entry = dictionary.lookup(segment)
            if entry is None or not entry.item_id or entry.confidence == "low":
                continue
            key = (segment, entry.item_id)
            if key in seen:
                continue
            seen.add(key)
            base = 40 if entry.layer == "custom" else 30
            if entry.confidence == "medium":
                base -= 10
            scores.append(
                RuleScore(
                    rule_id=f"DICT-{entry.layer.upper()}-{segment}",
                    item_id=entry.item_id,
                    priority=50,
                    source=SOURCE_ACCOUNT_DETAIL,
                    score=base,
                    summary_part=0,
                    account_part=base,
                    direction_compatible=True,
                    summary_hits=(),
                    account_hits=(segment,),
                    channels=(SOURCE_ACCOUNT_DETAIL,),
                    note_id=entry.note_id,
                )
            )
    return tuple(scores)