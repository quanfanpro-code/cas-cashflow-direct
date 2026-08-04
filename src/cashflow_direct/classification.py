from __future__ import annotations

import json
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


def _rule_matches(rule: ClassificationRule, component: CashflowComponent) -> bool:
    direction = "inflow" if component.cash_delta_cent > 0 else "outflow"
    if rule.direction not in {"any", direction}:
        return False
    text = "|".join(
        (component.summary, component.original_item_text, *component.counterpart_accounts)
    )
    if any(term in text for term in rule.exclude_terms):
        return False
    terms = rule.summary_terms + rule.account_terms
    return not terms or any(term in text for term in terms)


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

    matches = [rule for rule in rules.rules if _rule_matches(rule, component)]
    if not matches:
        raise ValueError(f"组成 {component.component_id} 未取得唯一系统首选")
    chosen = matches[0]
    item = rules.item_by_id[chosen.item_id]
    return ClassificationDecision(
        component_id=component.component_id,
        system_item_id=item.item_id,
        system_item_name=item.name,
        normal_direction=item.normal_direction,
        matched_rule_id=chosen.rule_id,
        reason=f"命中规则 {chosen.rule_id}；现金方向为{'流入' if component.cash_delta_cent > 0 else '流出'}",
        evidence_level=chosen.evidence_level,
        excluded_conflict_rule_ids=tuple(rule.rule_id for rule in matches[1:]),
    )


def classify_all(
    components: Sequence[CashflowComponent],
    rules: RulePack,
) -> tuple[ClassificationDecision, ...]:
    return tuple(classify_component(component, rules) for component in components)
