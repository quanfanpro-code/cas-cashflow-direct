from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.models import CashflowComponent, ClassificationDecision


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


def classify_component(
    component: CashflowComponent,
    rules: RulePack,
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
        )

    normalized_label = _normalize_item_name(component.original_item_text)
    exact_item = next(
        (
            item
            for item in rules.statement_items
            if item.is_leaf and normalized_label and _normalize_item_name(item.name) == normalized_label
        ),
        None,
    )
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
            )
        fallback = matches[0]
        item = rules.item_by_id[fallback.item_id]
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=item.item_id,
            system_item_name=item.name,
            normal_direction=item.normal_direction,
            matched_rule_id=fallback.rule_id,
            reason=(
                "业务信息及原标签均不足以判断，暂按现金方向归入其他经营活动项目；"
                f"现金为{'流入' if component.cash_delta_cent > 0 else '流出'}，证据较弱"
            ),
            evidence_level="low",
        )

    chosen = business_matches[0]
    item = rules.item_by_id[chosen.item_id]
    reason = _business_reason(chosen, component, item)
    high_item_ids = tuple(
        dict.fromkeys(
            rule.item_id
            for rule in business_matches
            if rule.evidence_level == "high"
        )
    )
    if len(high_item_ids) > 1:
        conflict_names = "、".join(
            f"“{rules.item_by_id[item_id].name}”" for item_id in high_item_ids
        )
        return ClassificationDecision(
            component_id=component.component_id,
            system_item_id=item.item_id,
            system_item_name=item.name,
            normal_direction=item.normal_direction,
            matched_rule_id="BUSINESS-RULE-CONFLICT",
            reason=f"{reason}；其他高证据同时指向{conflict_names}，业务证据存在冲突",
            evidence_level="medium",
            excluded_conflict_rule_ids=tuple(
                rule.rule_id
                for rule in business_matches
                if rule.item_id != chosen.item_id and rule.evidence_level == "high"
            ),
        )

    matched_rule_id = chosen.rule_id
    if exact_item is not None:
        if exact_item.item_id == item.item_id:
            reason += "；原标签一致，仅作补充"
        else:
            matched_rule_id = (
                "LABEL-BUSINESS-HIGH-CONFLICT"
                if chosen.evidence_level == "high"
                else "LABEL-BUSINESS-MEDIUM-CONFLICT"
            )
            reason += f"；原标签为“{exact_item.name}”，仅作为冲突备选"
    return ClassificationDecision(
        component_id=component.component_id,
        system_item_id=item.item_id,
        system_item_name=item.name,
        normal_direction=item.normal_direction,
        matched_rule_id=matched_rule_id,
        reason=reason,
        evidence_level=chosen.evidence_level,
        excluded_conflict_rule_ids=tuple(
            rule.rule_id
            for rule in business_matches[1:]
            if rule.item_id != chosen.item_id
        ),
    )


def classify_all(
    components: Sequence[CashflowComponent],
    rules: RulePack,
) -> tuple[ClassificationDecision, ...]:
    return tuple(classify_component(component, rules) for component in components)
