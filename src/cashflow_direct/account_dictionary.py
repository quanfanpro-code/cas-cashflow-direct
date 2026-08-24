# -*- coding: utf-8 -*-
"""完整科目路径语义。

通用词典只识别节点概念；固定程序组合完整父子路径后形成候选和质量。
只有用户确认且带 NOTE 编号的完整路径规则可以覆盖通用基线。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.decision_policy import EvidenceQuality
from cashflow_direct.evidence import (
    SOURCE_ACCOUNT_PATH,
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
    quality_score: int = 0

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
class AccountNodeConcept:
    level_index: int
    node_text: str
    concept: str
    source_text: str
    source: str = "direct"


@dataclass(frozen=True, slots=True)
class AccountPathSlot:
    level_index: int
    node_text: str
    allowed_concepts: tuple[str, ...]
    allowed_relations: tuple[str, ...] = (
        "用途",
        "对象",
        "资产属性",
        "费用性质",
        "非现金关系",
    )


@dataclass(frozen=True, slots=True)
class AccountPathRelation:
    parent_level_index: int
    child_level_index: int
    relation: str
    source: str = "agent"


@dataclass(frozen=True, slots=True)
class AccountSemanticRules:
    concepts: tuple[dict[str, object], ...]
    path_rules: tuple[dict[str, object], ...]
    inheritance_rules: tuple[dict[str, object], ...] = ()

    @property
    def allowed_concepts(self) -> tuple[str, ...]:
        return tuple(str(item["concept"]) for item in self.concepts)


@dataclass(frozen=True, slots=True)
class AccountPathSemanticResult:
    account: str
    status: str
    concepts: tuple[AccountNodeConcept, ...]
    candidate_item_ids: tuple[str, ...]
    inflow_candidate_item_ids: tuple[str, ...]
    outflow_candidate_item_ids: tuple[str, ...]
    quality: EvidenceQuality
    semantic: str
    basis: str
    unresolved_slots: tuple[AccountPathSlot, ...]
    matched_rule_ids: tuple[str, ...] = ()
    relations: tuple[AccountPathRelation, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountDictionary:
    entries: tuple[AccountSemanticEntry, ...]
    rules: AccountSemanticRules | None = None

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
        """只接受完整路径；不再用任一明细段越级覆盖父路径。"""
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
        for entry in self.entries:
            if entry.account == account_path and entry.layer == "runtime":
                return entry
        if self.rules is not None:
            result = analyze_account_path(account_path, self.rules)
            if (
                result.candidate_item_ids
                or result.inflow_candidate_item_ids
                or result.outflow_candidate_item_ids
            ):
                return _entry_from_path_result(result)
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
        quality_score=int(payload.get("quality_score", 0) or 0),
    )


def load_account_semantic_rules(root: Path) -> AccountSemanticRules:
    path = Path(root) / "references" / "科目语义词典.json"
    if not path.is_file():
        return AccountSemanticRules((), ())
    with path.open("r", encoding="utf-8-sig") as source:
        payload = json.load(source)
    return AccountSemanticRules(
        tuple(dict(item) for item in payload.get("concepts", ())),
        tuple(dict(item) for item in payload.get("path_rules", ())),
        tuple(dict(item) for item in payload.get("inheritance_rules", ())),
    )


def load_common_dictionary(root: Path) -> AccountDictionary:
    return AccountDictionary((), load_account_semantic_rules(root))


def merge_dictionaries(common: AccountDictionary, custom: AccountDictionary) -> AccountDictionary:
    return AccountDictionary(
        tuple(common.entries) + tuple(custom.entries),
        common.rules or custom.rules,
    )


def _concept_matches(node_text: str, term: str) -> bool:
    normalized = node_text.replace(" ", "").replace("-", "").replace("－", "")
    needle = term.replace(" ", "").replace("-", "").replace("－", "")
    return bool(needle and needle in normalized)


def _node_concepts(
    levels: tuple[str, ...],
    rules: AccountSemanticRules,
    agent_concepts: tuple[AccountNodeConcept, ...],
) -> tuple[AccountNodeConcept, ...]:
    found: list[AccountNodeConcept] = []
    seen: set[tuple[int, str]] = set()
    for level_index, node_text in enumerate(levels):
        for definition in rules.concepts:
            concept = str(definition["concept"])
            terms = tuple(str(value) for value in definition.get("terms", ()))
            hits = [term for term in terms if _concept_matches(node_text, term)]
            if hits and (level_index, concept) not in seen:
                source_text = max(hits, key=len)
                found.append(
                    AccountNodeConcept(
                        level_index,
                        node_text,
                        concept,
                        source_text,
                        str(definition.get("role", "direct")),
                    )
                )
                seen.add((level_index, concept))
    for item in agent_concepts:
        if item.level_index < 0 or item.level_index >= len(levels):
            continue
        if item.node_text != levels[item.level_index]:
            continue
        if (item.level_index, item.concept) not in seen:
            found.append(item)
            seen.add((item.level_index, item.concept))
    for level_index, node_text in enumerate(levels):
        if any(item.level_index == level_index for item in found):
            continue
        parent_concepts = {
            item.concept for item in found if item.level_index < level_index
        }
        for rule in rules.inheritance_rules:
            parent_concept = str(rule.get("parent_concept", ""))
            terms = tuple(str(value) for value in rule.get("terms", ()))
            hits = [term for term in terms if _concept_matches(node_text, term)]
            if parent_concept not in parent_concepts or not hits:
                continue
            concept = str(rule["concept"])
            found.append(
                AccountNodeConcept(
                    level_index,
                    node_text,
                    concept,
                    max(hits, key=len),
                    "parent_inheritance",
                )
            )
            seen.add((level_index, concept))
            break
    return tuple(found)


def _rule_matches(
    rule: dict[str, object],
    concepts: tuple[AccountNodeConcept, ...],
    level_count: int,
) -> bool:
    names = {item.concept for item in concepts}
    level1 = {item.concept for item in concepts if item.level_index == 0}
    required = {str(value) for value in rule.get("require_all", ())}
    any_of = {str(value) for value in rule.get("require_any", ())}
    forbidden = {str(value) for value in rule.get("forbid", ())}
    level1_required = {str(value) for value in rule.get("level1_all", ())}
    level1_any = {str(value) for value in rule.get("level1_any", ())}
    return bool(
        level_count >= int(rule.get("min_levels", 1))
        and required.issubset(names)
        and (not any_of or names.intersection(any_of))
        and not names.intersection(forbidden)
        and level1_required.issubset(level1)
        and (not level1_any or level1.intersection(level1_any))
    )


def _fixed_account_quality(
    concepts: tuple[AccountNodeConcept, ...],
    candidates: tuple[str, ...],
    inflow_candidates: tuple[str, ...],
    outflow_candidates: tuple[str, ...],
    unresolved_slots: tuple[AccountPathSlot, ...],
    matched_rule_ids: tuple[str, ...],
) -> EvidenceQuality:
    directional_max = max(
        len(candidates), len(inflow_candidates), len(outflow_candidates)
    )
    if not (candidates or inflow_candidates or outflow_candidates):
        return EvidenceQuality.WEAK if concepts else EvidenceQuality.INVALID
    if directional_max > 1:
        return EvidenceQuality.WEAK
    if unresolved_slots:
        return EvidenceQuality.MEDIUM
    # 决定性来自完整路径中的独立关系，而不是任一节点词或外部自报把握。
    concept_names = {item.concept for item in concepts}
    decisive = {
        "staff_cost",
        "long_asset_parent",
        "borrowing_principal",
        "investment_principal",
        "capital_principal",
        "interest_payment",
        "bank_interest_income",
        "sales_business",
        "purchase_inventory",
        "penalty",
        "repurchase_obligation",
        "employee_advance",
        "other_tax",
    }
    if concept_names.intersection(decisive):
        return EvidenceQuality.STRONG
    if (
        {"long_asset_detail", "engineering_payment"}.issubset(concept_names)
        and not concept_names.intersection(
            {"operating_expense_parent", "production_parent", "repair"}
        )
    ):
        return EvidenceQuality.STRONG
    if matched_rule_ids:
        return EvidenceQuality.MEDIUM
    return EvidenceQuality.WEAK


def analyze_account_path(
    account_path: str,
    rules: AccountSemanticRules,
    agent_concepts: tuple[AccountNodeConcept, ...] = (),
    agent_relations: tuple[AccountPathRelation, ...] = (),
) -> AccountPathSemanticResult:
    """按完整父子路径形成候选；任何单段概念都不能直接返回项目。"""
    levels = split_account_levels(account_path)
    concepts = _node_concepts(levels, rules, agent_concepts)
    recognized_levels = {item.level_index for item in concepts}
    unresolved_slots = tuple(
        AccountPathSlot(index, node, rules.allowed_concepts)
        for index, node in enumerate(levels)
        if index not in recognized_levels
    )
    matches = tuple(
        rule
        for rule in rules.path_rules
        if _rule_matches(rule, concepts, len(levels))
    )
    if any(bool(rule.get("stop")) for rule in matches):
        matches = tuple(rule for rule in matches if bool(rule.get("stop")))
    candidates = tuple(
        dict.fromkeys(
            str(value)
            for rule in matches
            for value in rule.get("candidate_item_ids", ())
        )
    )
    inflow = tuple(
        dict.fromkeys(
            str(value)
            for rule in matches
            for value in rule.get("inflow_candidate_item_ids", ())
        )
    )
    outflow = tuple(
        dict.fromkeys(
            str(value)
            for rule in matches
            for value in rule.get("outflow_candidate_item_ids", ())
        )
    )
    matched_rule_ids = tuple(str(rule["rule_id"]) for rule in matches)
    quality = _fixed_account_quality(
        concepts,
        candidates,
        inflow,
        outflow,
        unresolved_slots,
        matched_rule_ids,
    )
    if not matches:
        status = "未识别" if not concepts else "部分解释"
    elif max(len(candidates), len(inflow), len(outflow)) > 1:
        status = "冲突"
    elif unresolved_slots:
        status = "部分解释"
    elif agent_concepts or agent_relations:
        status = "Agent补充"
    else:
        status = "固定规则完整解释"
    semantic = "；".join(
        dict.fromkeys(str(rule.get("semantic", "")) for rule in matches if rule.get("semantic"))
    )
    concept_basis = "、".join(
        f"第{item.level_index + 1}层“{item.source_text}”={item.concept}（{item.source}）"
        for item in concepts
    )
    rule_basis = "、".join(matched_rule_ids) or "未形成完整路径规则"
    relation_basis = "、".join(
        f"第{item.parent_level_index + 1}层→第{item.child_level_index + 1}层={item.relation}"
        for item in agent_relations
    )
    basis = (
        f"完整路径“{account_path}”；节点概念：{concept_basis or '无'}；"
        f"父子关系：{relation_basis or '按路径层级'}；路径规则：{rule_basis}"
    )
    return AccountPathSemanticResult(
        account_path,
        status,
        concepts,
        candidates,
        inflow,
        outflow,
        quality,
        semantic,
        basis,
        unresolved_slots,
        matched_rule_ids,
        agent_relations,
    )


def build_account_agent_task(result: AccountPathSemanticResult) -> dict[str, object]:
    """只请求缺失的节点概念与父子关系，不请求项目或分数。"""
    return {
        "account": result.account,
        "account_levels": list(split_account_levels(result.account)),
        "recognized_concepts": [
            {
                "level_index": item.level_index,
                "node_text": item.node_text,
                "source_text": item.source_text,
                "concept": item.concept,
            }
            for item in result.concepts
        ],
        "unresolved_slots": [
            {
                "level_index": item.level_index,
                "node_text": item.node_text,
                "allowed_concepts": list(item.allowed_concepts),
                "allowed_relations": list(item.allowed_relations),
            }
            for item in result.unresolved_slots
        ],
        "instruction": (
            "只补节点原文中的受控概念和父子关系；不得返回现金流项目、候选、质量、分数、"
            "置信度、重要性或处理动作。"
        ),
    }


def merge_account_agent_concepts(
    result: AccountPathSemanticResult,
    payload: dict[str, object],
    rules: AccountSemanticRules,
) -> AccountPathSemanticResult:
    """校验受限 Agent 答案，再由固定程序重算候选和质量。"""
    forbidden = {
        "item_id",
        "candidate_item_ids",
        "inflow_item_id",
        "outflow_item_id",
        "inflow_candidate_item_ids",
        "outflow_candidate_item_ids",
        "confidence",
        "quality",
        "score",
        "materiality",
        "action",
    }
    if forbidden.intersection(payload):
        raise ValueError("科目路径Agent不得返回项目、质量或分数")
    levels = split_account_levels(result.account)
    allowed = set(rules.allowed_concepts)
    unresolved_level_indexes = {
        slot.level_index for slot in result.unresolved_slots
    }
    additions: list[AccountNodeConcept] = []
    for raw in payload.get("node_concepts", ()):
        item = dict(raw)
        level_index = int(item.get("level_index", -1))
        node_text = str(item.get("node_text", ""))
        source_text = str(item.get("source_text", ""))
        concept = str(item.get("concept", ""))
        if level_index not in unresolved_level_indexes:
            raise ValueError("科目路径Agent只能补未识别节点")
        if (
            level_index < 0
            or level_index >= len(levels)
            or node_text != levels[level_index]
            or not source_text
            or source_text not in node_text
            or concept not in allowed
        ):
            raise ValueError("科目路径Agent结果无法回指原节点或包含未知概念")
        additions.append(
            AccountNodeConcept(
                level_index,
                node_text,
                concept,
                source_text,
                "agent",
            )
        )
    allowed_relations = {"用途", "对象", "资产属性", "费用性质", "非现金关系"}
    relations: list[AccountPathRelation] = []
    for raw in payload.get("relations", ()):
        relation = dict(raw)
        relation_name = str(relation.get("relation", ""))
        if relation_name not in allowed_relations:
            raise ValueError("科目路径Agent结果包含未知父子关系")
        parent = int(relation.get("parent_level_index", -1))
        child = int(relation.get("child_level_index", -1))
        if parent < 0 or child < 0 or parent >= len(levels) or child >= len(levels):
            raise ValueError("科目路径Agent结果的层级序号无效")
        relations.append(AccountPathRelation(parent, child, relation_name))
    merged = tuple(result.concepts) + tuple(additions)
    return analyze_account_path(result.account, rules, merged, tuple(relations))


def _entry_from_path_result(result: AccountPathSemanticResult) -> AccountSemanticEntry:
    direct = result.candidate_item_ids[0] if len(result.candidate_item_ids) == 1 else ""
    inflow = (
        result.inflow_candidate_item_ids[0]
        if len(result.inflow_candidate_item_ids) == 1
        else ""
    )
    outflow = (
        result.outflow_candidate_item_ids[0]
        if len(result.outflow_candidate_item_ids) == 1
        else ""
    )
    return AccountSemanticEntry(
        account=result.account,
        semantic=result.semantic,
        item_id=direct,
        basis=result.basis,
        confidence="",
        layer="common",
        inflow_item_id=inflow,
        outflow_item_id=outflow,
        classification_facts=tuple(
            f"account_concept:{item.concept}" for item in result.concepts
        ),
        candidate_item_ids=result.candidate_item_ids,
        inflow_candidate_item_ids=result.inflow_candidate_item_ids,
        outflow_candidate_item_ids=result.outflow_candidate_item_ids,
        quality_score=result.quality.value,
    )


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
        quality = (
            EvidenceQuality(entry.quality_score)
            if entry.quality_score in {0, 10, 25, 45}
            else EvidenceQuality.INVALID
        )
        # 仅为历史企业自定义条目保留兼容；通用路径一律使用固定程序给出的 quality_score。
        if not entry.quality_score and entry.layer == "custom":
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
    path_candidates = tuple(
        dict.fromkeys(
            candidate
            for score in scores
            for candidate in (score.candidate_item_ids or (score.item_id,))
            if candidate
        )
    )
    if len(path_candidates) > 1:
        return (
            RuleScore(
                rule_id=f"DICT-PATH-CONFLICT-{component.component_id}",
                item_id="",
                priority=50,
                source=SOURCE_ACCOUNT_PATH,
                score=EvidenceQuality.WEAK.value,
                summary_part=0,
                account_part=EvidenceQuality.WEAK.value,
                direction_compatible=True,
                summary_hits=(),
                account_hits=tuple(
                    dict.fromkeys(hit for score in scores for hit in score.account_hits)
                ),
                channels=(SOURCE_ACCOUNT_PATH,),
                account_facts=tuple(
                    dict.fromkeys(fact for score in scores for fact in score.account_facts)
                ),
                candidate_item_ids=path_candidates,
            ),
        )
    return tuple(scores)
