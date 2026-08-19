from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.account_dictionary import score_dictionary_hits
from cashflow_direct.evidence import (
    SOURCE_ACCOUNT_DETAIL,
    SOURCE_ACCOUNT_LEVEL1,
    SOURCE_SUMMARY,
    aggregate_evidence,
    score_rule,
)
from cashflow_direct.models import CashflowComponent, ClassificationDecision

# 来源中文名映射（用于 reason 展示，最终 xlsx 人类可读列一律中文）
_SOURCE_CN = {
    SOURCE_SUMMARY: "摘要",
    SOURCE_ACCOUNT_DETAIL: "对方科目明细",
    SOURCE_ACCOUNT_LEVEL1: "对方科目一级",
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
        term for term in rule.account_terms if term in account_text
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
) -> ClassificationDecision:
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
            evidence_score=100,
        )

    exact_item = standardize_flow_item(component.original_item_text, rules)
    matches = [rule for rule in rules.rules if _rule_matches(rule, component)]
    if not matches:
        raise ValueError(f"组成 {component.component_id} 未取得唯一系统首选")
    business_matches = [
        rule for rule in matches if rule.summary_terms or rule.account_terms
    ]
    if not business_matches:
        if exact_item is not None:
            return ClassificationDecision(
                component_id=component.component_id,
                system_item_id=exact_item.item_id,
                system_item_name=exact_item.name,
                normal_direction=exact_item.normal_direction,
                matched_rule_id="ORIGINAL-LABEL-FALLBACK",
                reason="摘要和对方科目不足以判断，暂按原现流项目保底分类，证据较弱",
                evidence_level="low",
                evidence_score=0,
            )
        fallback = matches[0]
        item = rules.item_by_id[fallback.item_id]
        if fallback.sole_account_terms:
            # 唯一对方科目规则：摘要无业务线索，但对方科目全部属于某一类（如应交税费），
            # 证据等级取规则自身声明（中证据），并走人工复核
            reason = (
                "摘要无明确业务线索，但对方科目唯一且均属"
                f"“{'、'.join(fallback.sole_account_terms)}”类，因此判断为“{item.name}”"
            )
        else:
            reason = (
                "业务信息及原标签均不足以判断，暂按现金方向归入其他经营活动项目"
                "（此为内部保守处理口径，不是准则的直接结论）；"
                f"现金为{'流入' if component.cash_delta_cent > 0 else '流出'}，证据较弱"
            )
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=item.item_id,
            system_item_name=item.name,
            normal_direction=item.normal_direction,
            matched_rule_id=fallback.rule_id,
            reason=reason,
            evidence_level=fallback.evidence_level,
            evidence_score=0,
        )

    # 业务规则命中：按证据打分决策（可并入科目语义词典的明细层得分）
    rule_scores = [
        score_rule(rule, component, rules.item_by_id[rule.item_id].normal_direction)
        for rule in business_matches
    ]
    if dictionary is not None:
        rule_scores.extend(score_dictionary_hits(component, dictionary))
    agg = aggregate_evidence(rule_scores)
    if agg is None:
        # 没有任何规则实际命中打分（理论上不会发生，兜底走无命中保底）
        if exact_item is not None:
            return ClassificationDecision(
                component_id=component.component_id,
                system_item_id=exact_item.item_id,
                system_item_name=exact_item.name,
                normal_direction=exact_item.normal_direction,
                matched_rule_id="ORIGINAL-LABEL-FALLBACK",
                reason="摘要和对方科目不足以判断，暂按原现流项目保底分类，证据较弱",
                evidence_level="low",
                evidence_score=0,
            )
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=matches[0].item_id,
            system_item_name=rules.item_by_id[matches[0].item_id].name,
            normal_direction=rules.item_by_id[matches[0].item_id].normal_direction,
            matched_rule_id=matches[0].rule_id,
            reason="业务信息及原标签均不足以判断，暂按现金方向归入其他经营活动项目（此为内部保守处理口径，不是准则的直接结论）",
            evidence_level="low",
            evidence_score=0,
        )

    rule_by_id = {rule.rule_id: rule for rule in business_matches}
    best_score = min(
        (score for score in agg.rule_scores if score.item_id == agg.item_id),
        key=lambda item: (-item.score, item.priority, item.rule_id),
    )
    item = rules.item_by_id[agg.item_id]
    chosen_rule = rule_by_id.get(best_score.rule_id)
    if chosen_rule is not None:
        reason = _business_reason(chosen_rule, component, item)
    else:
        # 词典命中的专属规则没有 rule JSON 实体，命中词即对方科目明细段
        account_text = "、".join(best_score.account_hits) if best_score.account_hits else ""
        reason = (
            f"对方科目包含“{account_text}”，符合“{item.name}”的科目语义词典定义"
        )
        if best_score.note_id:
            # 复核修复：公司特殊规则命中必须留 NOTE 编号痕迹，保证理由可追查
            reason += f"；依据公司特殊规则：{best_score.note_id}"
    reason += f"；证据得分{agg.total}（{'/'.join(_SOURCE_CN[source] for source in agg.sources)}）"
    evidence_level = agg.tier
    excluded_conflict_rule_ids = tuple(
        score.rule_id for score in agg.rule_scores if score.item_id != agg.item_id
    )
    matched_rule_id = best_score.rule_id

    # 1) 冲突：多源指向不同项目，Resolution 送回复核
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
            resolved=False,
            evidence_score=agg.total,
            evidence_sources=agg.sources,
        )

    # 2) 无原标签且非冲突：用首选项目
    if exact_item is None:
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=agg.item_id,
            system_item_name=item.name,
            normal_direction=item.normal_direction,
            matched_rule_id=matched_rule_id,
            reason=reason,
            evidence_level=evidence_level,
            excluded_conflict_rule_ids=excluded_conflict_rule_ids,
            evidence_score=agg.total,
            evidence_sources=agg.sources,
        )

    # 3) 原标签与首选一致
    if exact_item.item_id == agg.item_id:
        reason += "；原标签一致，仅作补充"
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=agg.item_id,
            system_item_name=item.name,
            normal_direction=item.normal_direction,
            matched_rule_id=matched_rule_id,
            reason=reason,
            evidence_level=evidence_level,
            excluded_conflict_rule_ids=excluded_conflict_rule_ids,
            evidence_score=agg.total,
            evidence_sources=agg.sources,
        )

    # 4) 原标签不一致：达双高门槛才改判，否则保留原标签送复核
    if agg.can_override_label:
        reason += (
            f"；原标签为“{exact_item.name}”，证据充分"
            f"（得分{agg.total}，{len(agg.sources)}个来源印证），予以改判"
        )
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=agg.item_id,
            system_item_name=item.name,
            normal_direction=item.normal_direction,
            matched_rule_id="LABEL-BUSINESS-OVERRIDE",
            reason=reason,
            evidence_level=evidence_level,
            excluded_conflict_rule_ids=excluded_conflict_rule_ids,
            evidence_score=agg.total,
            evidence_sources=agg.sources,
        )

    reason += (
        f"；原标签为“{exact_item.name}”，现有证据得分{agg.total}、"
        f"来源{len(agg.sources)}个，不足以推翻原标签，保留原标签并送复核"
    )
    return ClassificationDecision(
        component_id=component.component_id,
        system_item_id=exact_item.item_id,
        system_item_name=exact_item.name,
        normal_direction=exact_item.normal_direction,
        matched_rule_id="LABEL-KEPT-INSUFFICIENT-EVIDENCE",
        reason=reason,
        evidence_level=evidence_level,
        excluded_conflict_rule_ids=excluded_conflict_rule_ids,
        resolved=False,
        label_kept=True,
        evidence_score=agg.total,
        evidence_sources=agg.sources,
    )


def classify_all(
    components: Sequence[CashflowComponent],
    rules: RulePack,
    dictionary: object | None = None,
) -> tuple[ClassificationDecision, ...]:
    return tuple(classify_component(component, rules, dictionary) for component in components)
