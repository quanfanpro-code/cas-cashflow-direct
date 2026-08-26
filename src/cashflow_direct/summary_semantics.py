from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.decision_policy import EvidenceQuality
from cashflow_direct.rule_registry import load_rule_registry


_LEXICON_SECTIONS = (
    "cash_actions",
    "business_relations",
    "business_objects",
    "counterparty_roles",
    "document_contexts",
    "modifiers",
    "purposes_attributes",
)
_FORBIDDEN_AGENT_FIELDS = {
    "item_id",
    "candidate_item_ids",
    "quality",
    "score",
    "action",
    "confidence",
    "evidence_level",
    "original_item",
    "account_path",
    "amount",
}


@dataclass(frozen=True, slots=True)
class SummarySpan:
    slot: str
    text: str
    start: int
    end: int
    source: str = "rule"


@dataclass(frozen=True, slots=True)
class SummarySemanticResult:
    summary: str
    status: str
    spans: tuple[SummarySpan, ...]
    candidate_item_ids: tuple[str, ...]
    quality: EvidenceQuality
    reason: str
    unresolved_slots: tuple[str, ...] = ()
    unexplained_spans: tuple[SummarySpan, ...] = ()


@dataclass(frozen=True, slots=True)
class _Fact:
    slot: str
    value: str
    text: str
    start: int
    end: int
    source: str = "rule"

    def public(self) -> SummarySpan:
        return SummarySpan(self.slot, self.text, self.start, self.end, self.source)


def load_summary_rules(project_root: Path) -> dict[str, object]:
    root = Path(project_root)
    registry = load_rule_registry(root)
    rules = registry.summary_semantics
    lexicons = rules.get("lexicons")
    if not isinstance(lexicons, dict) or tuple(lexicons) != _LEXICON_SECTIONS:
        raise ValueError("摘要语义规则必须按约定包含七个词典分区")
    if not rules.get("schema_version") or not isinstance(rules.get("candidate_rules"), list):
        raise ValueError("摘要语义规则缺少版本或候选组合规则")

    statement = registry.statement_policy
    leaf_ids = {
        item["item_id"]
        for item in statement.get("statement_items", [])
        if item.get("is_leaf")
    }
    covered = {
        item_id
        for rule in rules["candidate_rules"]
        for item_id in rule.get("candidate_item_ids", [])
    }
    if covered != leaf_ids:
        raise ValueError("摘要语义候选组合规则必须完整覆盖22个正表叶子项目")
    return rules


def _overlaps(start: int, end: int, occupied: Sequence[tuple[int, int]]) -> bool:
    return any(start < right and end > left for left, right in occupied)


def _noise_facts(summary: str, rules: Mapping[str, object]) -> list[_Fact]:
    facts: list[_Fact] = []
    occupied: list[tuple[int, int]] = []
    for spec in rules.get("noise_patterns", []):
        pattern = spec.get("pattern", "")
        slot = spec.get("slot", "noise")
        for match in re.finditer(pattern, summary, re.IGNORECASE):
            if not _overlaps(match.start(), match.end(), occupied):
                facts.append(_Fact(slot, slot, match.group(0), match.start(), match.end()))
                occupied.append((match.start(), match.end()))
    return facts


def _all_action_terms(rules: Mapping[str, object]) -> tuple[str, ...]:
    lexicons = rules["lexicons"]
    return tuple(
        sorted(
            {
                term
                for entry in lexicons["cash_actions"]
                for term in entry.get("terms", [])
            },
            key=len,
            reverse=True,
        )
    )


def _entity_facts(summary: str, rules: Mapping[str, object]) -> list[_Fact]:
    suffix_pattern = rules.get(
        "organization_suffix_pattern",
        r"(?:有限责任公司|股份有限公司|有限公司|公司|银行|中心|事务所|合作社|工厂)",
    )
    action_terms = _all_action_terms(rules)
    structural_boundaries = ("向", "给", "由", "从", "，", ",", "。", ";", "；", ":", "：")
    non_entities = set(rules.get("non_entity_organization_terms", []))
    non_entity_follow = tuple(rules.get("organization_suffix_non_entity_follow", []))
    facts: list[_Fact] = []
    for match in re.finditer(suffix_pattern, summary):
        if any(
            summary[max(0, match.end() - len(term)) : match.end()] == term
            for term in non_entities
        ):
            continue
        left = max(0, match.start() - 30)
        prefix = summary[left : match.start()]
        boundary_end = 0
        for token in structural_boundaries:
            position = prefix.rfind(token)
            if position >= 0:
                boundary_end = max(boundary_end, position + len(token))
        segment = prefix[boundary_end:]
        action_starts = sorted(
            (segment.find(term), len(term))
            for term in action_terms
            if segment.find(term) >= 0
        )
        if action_starts:
            first_start, first_length = action_starts[0]
            boundary_end += first_start + first_length
        elif segment.startswith("付"):
            boundary_end += 1
        start = left + boundary_end
        while start < match.end() and summary[start] in " 　0123456789年月日-_/":
            start += 1
        entity_text = summary[start : match.end()]
        followed_by_business_noun = any(
            summary.startswith(term, match.end()) for term in non_entity_follow
        )
        if (
            match.end() - start >= 2
            and entity_text not in non_entities
            and not followed_by_business_noun
        ):
            facts.append(
                _Fact(
                    "counterparty_entity",
                    "organization",
                    entity_text,
                    start,
                    match.end(),
                )
            )
    return facts


def _term_occurrences(summary: str, term: str) -> list[tuple[int, int]]:
    positions: list[tuple[int, int]] = []
    start = 0
    while True:
        index = summary.find(term, start)
        if index < 0:
            return positions
        positions.append((index, index + len(term)))
        start = index + 1


def _lexicon_facts(
    summary: str,
    rules: Mapping[str, object],
    protected: Sequence[tuple[int, int]],
) -> list[_Fact]:
    matches: list[tuple[int, int, str, str, str]] = []
    lexicons = rules["lexicons"]
    for section in _LEXICON_SECTIONS:
        for entry in lexicons[section]:
            slot = entry["slot"]
            value = entry["value"]
            for term in entry.get("terms", []):
                for start, end in _term_occurrences(summary, term):
                    if not _overlaps(start, end, protected):
                        matches.append((start, end, slot, value, term))

    occupied_by_slot: dict[str, list[tuple[int, int]]] = {}
    facts: list[_Fact] = []
    for start, end, slot, value, term in sorted(
        matches,
        key=lambda match: (-(match[1] - match[0]), match[0], match[2], match[3]),
    ):
        occupied = occupied_by_slot.setdefault(slot, [])
        if _overlaps(start, end, occupied):
            continue
        facts.append(_Fact(slot, value, term, start, end))
        occupied.append((start, end))
    return facts


def _anchored_cash_action_fact(
    summary: str,
    facts: Sequence[_Fact],
    rules: Mapping[str, object],
) -> _Fact | None:
    for specification in rules.get("anchored_cash_actions", ()):
        if not isinstance(specification, Mapping):
            continue
        match = re.match(str(specification.get("pattern", "")), summary)
        if match is None:
            continue
        start, end = match.span(1)
        if any(
            fact.slot == "cash_action"
            and _overlaps(start, end, ((fact.start, fact.end),))
            for fact in facts
        ):
            return None
        return _Fact(
            "cash_action",
            str(specification.get("value", "")),
            match.group(1),
            start,
            end,
        )
    return None


def _inside_parentheses(summary: str, position: int) -> bool:
    left = max(summary.rfind("（", 0, position), summary.rfind("(", 0, position))
    right = max(summary.rfind("）", 0, position), summary.rfind(")", 0, position))
    return left > right


def _apply_action_conditioned_object_overrides(
    facts: Sequence[_Fact],
    rules: Mapping[str, object],
) -> list[_Fact]:
    actions = {
        fact.value for fact in facts if fact.slot == "cash_action"
    }
    normalized = list(facts)
    for specification in sorted(
        (
            item
            for item in rules.get("action_conditioned_object_overrides", ())
            if isinstance(item, Mapping) and item.get("status", "active") == "active"
        ),
        key=lambda item: int(item.get("priority", 0)),
    ):
        if str(specification.get("cash_action", "")) not in actions:
            continue
        source_value = str(specification.get("from_value", ""))
        target_value = str(specification.get("to_value", ""))
        terms = {str(term) for term in specification.get("terms", ())}
        normalized = [
            _Fact(
                fact.slot,
                target_value,
                fact.text,
                fact.start,
                fact.end,
                fact.source,
            )
            if fact.slot == "business_object"
            and fact.value == source_value
            and fact.text in terms
            else fact
            for fact in normalized
        ]
    return normalized


def _normalize_nested_actions(
    summary: str,
    facts: Sequence[_Fact],
    rules: Mapping[str, object],
) -> list[_Fact]:
    anchored = _anchored_cash_action_fact(summary, facts, rules)
    if anchored is not None:
        facts = (*facts, anchored)
    actions = sorted((fact for fact in facts if fact.slot == "cash_action"), key=lambda fact: fact.start)
    if len(actions) < 2:
        return list(facts)
    relations = [fact for fact in facts if fact.slot == "business_relation"]
    # “取得子公司支付现金”中的“取得”描述交易关系，不是另一条现金流动作。
    relation_actions = {
        action
        for action in actions[:-1]
        if action.value == "inflow"
        and any(
            relation.value == "acquire"
            and relation.start == action.start
            and relation.end == action.end
            for relation in relations
        )
        and any(later.value == "outflow" for later in actions if later.start > action.end)
    }
    if relation_actions:
        facts = [fact for fact in facts if fact not in relation_actions]
        actions = [action for action in actions if action not in relation_actions]
        if len(actions) < 2:
            return list(facts)
    roles = [fact for fact in facts if fact.slot == "counterparty_role"]
    entities = [fact for fact in facts if fact.slot == "counterparty_entity"]
    business_objects = [fact for fact in facts if fact.slot == "business_object"]
    primary = actions[0]
    nested = {
        action
        for action in actions[1:]
        if any(
            primary.end <= marker.start < action.start
            for marker in (*roles, *entities)
        )
        and action.value != primary.value
        or (
            primary.value == "inflow"
            and action.value == "outflow"
            and action.text in {"退回", "退还"}
        )
        or (
            action.text == "回款"
            and primary.value == "outflow"
            and (
                bool(
                    re.search(
                        r"(?:沟通|协调|催收|跟进|对账|合同|项目|事项|业务拓展)[^，,。；;]{0,10}$",
                        summary[max(0, action.start - 16) : action.start],
                    )
                )
                or bool(
                    re.match(
                        r"(?:事宜|事项|协调|沟通|挂账)",
                        summary[action.end : action.end + 4],
                    )
                )
            )
        )
        or (
            action.text == "支出"
            and summary[action.end : action.end + 1] == "户"
        )
        or (
            action.text in {"收款", "付款"}
            and (
                summary[max(0, action.start - 1) : action.start] in {"验", "赔"}
                or bool(re.search(r"第?\d+次$", summary[max(0, action.start - 6) : action.start]))
            )
        )
        or (
            bool(re.search(r"(?:前期)?已$", summary[max(0, action.start - 4) : action.start]))
            or (
                action.text in {"支付", "付款"}
                and bool(re.search(r"后(?:\d+个?月)?$", summary[max(0, action.start - 8) : action.start]))
            )
            or _inside_parentheses(summary, action.start)
        )
        or (
            primary.value == "outflow"
            and action.text == "收到"
            and bool(
                re.match(
                    r"[^，,。；;]{0,8}(?:发票|火车票|机票|票据|收据|证书)",
                    summary[action.end : action.end + 12],
                )
            )
        )
        or (
            primary.value == "outflow"
            and action.text == "取得"
            and bool(re.search(r"(?:未|已)$", summary[max(0, action.start - 3) : action.start]))
        )
        or (
            primary.value == "inflow"
            and action.value == "outflow"
            and not any(
                primary.end <= business.start < action.start
                for business in business_objects
            )
            and bool(re.fullmatch(r"[\u4e00-\u9fff·]{2,4}", summary[primary.end : action.start]))
        )
    }
    return [
        _Fact("counterparty_action", fact.value, fact.text, fact.start, fact.end, fact.source)
        if fact in nested
        else fact
        for fact in facts
    ]


def _normalize_business_relations(
    summary: str,
    facts: Sequence[_Fact],
) -> list[_Fact]:
    """先绑定复合名词和服务对象，避免孤立关键词越级形成候选。"""
    normalized = list(facts)

    wage_account_spans = tuple(
        match.span()
        for match in re.finditer(
            r"(?:农民工?)?工资(?:专用)?(?:账户|专户)|工资保证金",
            summary,
        )
    )
    if wage_account_spans:
        normalized = [
            fact
            for fact in normalized
            if not (
                fact.slot == "business_object"
                and fact.value == "staff_compensation"
                and _overlaps(fact.start, fact.end, wage_account_spans)
            )
        ]
        normalized.extend(
            _Fact("attribute", "restricted_account", summary[start:end], start, end)
            for start, end in wage_account_spans
        )

    employee_role = any(
        fact.slot == "counterparty_role" and fact.value == "employee"
        for fact in normalized
    )
    advance_match = re.search(
        r"(?:退回|归还|收回)[^，,。；;]{0,6}(借款|借支)|"
        r"(借款|借支)[^，,。；;]{0,6}(?:退回|归还)",
        summary,
    )
    if employee_role and advance_match is not None:
        normalized = [
            fact
            for fact in normalized
            if not (
                (fact.slot == "business_object" and fact.value == "borrowing")
                or (fact.slot == "business_relation" and fact.value == "borrow")
            )
        ]
        start, end = next(
            span
            for index in (1, 2)
            if (span := advance_match.span(index)) != (-1, -1)
        )
        normalized.append(
            _Fact("business_object", "employee_advance", summary[start:end], start, end)
        )

    trade_object = any(
        fact.slot == "business_object" and fact.value == "trade_goods"
        for fact in normalized
    )
    asset_match = re.search(r"设备|固定资产|无形资产|生产线|在建工程|软件", summary)
    if trade_object and asset_match is not None:
        normalized.append(
            _Fact(
                "business_object",
                "long_asset_acquisition",
                asset_match.group(0),
                asset_match.start(),
                asset_match.end(),
            )
        )
    return normalized


def _facts_by_slot(facts: Sequence[_Fact]) -> dict[str, set[str]]:
    values: dict[str, set[str]] = {}
    for fact in facts:
        values.setdefault(fact.slot, set()).add(fact.value)
    return values


def _rule_matches(rule: Mapping[str, object], values: Mapping[str, set[str]]) -> bool:
    required = rule.get("all", {})
    excluded = rule.get("without", {})
    return all(
        values.get(slot, set()).intersection(allowed)
        for slot, allowed in required.items()
    ) and not any(
        values.get(slot, set()).intersection(disallowed)
        for slot, disallowed in excluded.items()
    )


def _candidate_ids(facts: Sequence[_Fact], rules: Mapping[str, object]) -> tuple[str, ...]:
    values = _facts_by_slot(facts)
    candidates = {
        item_id
        for rule in rules["candidate_rules"]
        if _rule_matches(rule, values)
        for item_id in rule["candidate_item_ids"]
    }
    return tuple(sorted(candidates))


def _unexplained_business_spans(
    summary: str,
    facts: Sequence[_Fact],
    rules: Mapping[str, object],
) -> tuple[SummarySpan, ...]:
    covered = [False] * len(summary)
    for fact in facts:
        for index in range(fact.start, fact.end):
            covered[index] = True

    patterns = tuple(
        str(pattern)
        for pattern in (
            *rules.get("agent_trigger_patterns", ()),
            *rules.get("unexplained_business_patterns", ()),
        )
    )
    spans: list[SummarySpan] = []
    for chinese_run in re.finditer(r"[\u4e00-\u9fff]+", summary):
        start = chinese_run.start()
        while start < chinese_run.end():
            while start < chinese_run.end() and covered[start]:
                start += 1
            end = start
            while end < chinese_run.end() and not covered[end]:
                end += 1
            text = summary[start:end]
            if len(text) >= 2 and any(re.search(pattern, text) for pattern in patterns):
                spans.append(SummarySpan("unexplained", text, start, end))
            start = end
    return tuple(spans)


def _unresolved_slots(
    facts: Sequence[_Fact],
    unexplained_spans: Sequence[SummarySpan],
) -> tuple[str, ...]:
    actions = [fact for fact in facts if fact.slot == "cash_action"]
    resolved_slots = {fact.slot for fact in facts}
    unresolved: list[str] = []
    if len({fact.value for fact in actions}) > 1 and "clause_binding" not in resolved_slots:
        unresolved.append("clause_binding")
    if unexplained_spans:
        if not actions:
            unresolved.append("cash_action")
        unresolved.extend(
            (
                "business_relation",
                "business_object",
                "counterparty_role",
                "purpose",
                "attribute",
                "clause_binding",
            )
        )
    return tuple(dict.fromkeys(unresolved))


def _quality(
    facts: Sequence[_Fact],
    candidates: Sequence[str],
    status: str,
    rules: Mapping[str, object],
) -> EvidenceQuality:
    values = _facts_by_slot(facts)
    meaningful_slots = {"cash_action", "business_relation", "business_object", "refund", "purpose", "attribute"}
    if status == "invalid" or not meaningful_slots.intersection(values):
        return EvidenceQuality.INVALID
    if status == "needs_agent":
        return EvidenceQuality.INVALID
    if {"conditional", "uncertainty", "negation"}.intersection(values) or len(candidates) > 1:
        return EvidenceQuality.WEAK
    if not candidates:
        return EvidenceQuality.WEAK

    quality_rules = rules.get("quality_rules", {})
    strong_values = quality_rules.get("strong_values", {})
    has_decisive_fact = any(
        values.get(slot, set()).intersection(allowed)
        for slot, allowed in strong_values.items()
    )
    if len(candidates) == 1 and "cash_action" in values and "business_object" in values and has_decisive_fact:
        return EvidenceQuality.STRONG
    return EvidenceQuality.MEDIUM


def _reason(status: str, candidates: Sequence[str], quality: EvidenceQuality) -> str:
    if status == "invalid":
        return "摘要为空或只有无业务含义的编号，不能形成有效候选"
    if status == "needs_agent":
        return "固定规则无法确定语言槽位，等待受限Agent补充原文区间"
    if len(candidates) > 1:
        return f"摘要形成多个合理候选：{'、'.join(candidates)}"
    if len(candidates) == 1:
        return f"摘要固定规则形成候选{candidates[0]}，证据质量{quality.value}分"
    return f"摘要只形成部分业务语义，证据质量{quality.value}分"


def _analyze(
    summary: str,
    rules: Mapping[str, object],
    agent_facts: Sequence[_Fact] = (),
) -> SummarySemanticResult:
    if not isinstance(summary, str):
        raise TypeError("摘要必须是字符串")
    noise = _noise_facts(summary, rules)
    entities = _entity_facts(summary, rules)
    protected = [(fact.start, fact.end) for fact in (*noise, *entities)]
    lexical = _lexicon_facts(summary, rules, protected)
    lexical = [
        fact
        for fact in lexical
        if not (
            fact.slot == "conditional"
            and fact.text == "待"
            and summary[max(0, fact.start - 1) : fact.start] == "招"
        )
    ]
    facts = _normalize_nested_actions(
        summary,
        (*noise, *entities, *lexical, *agent_facts),
        rules,
    )
    facts = _apply_action_conditioned_object_overrides(facts, rules)
    facts = _normalize_business_relations(summary, facts)
    unexplained = _unexplained_business_spans(summary, facts, rules)
    unresolved = _unresolved_slots(facts, unexplained)
    meaningful = [
        fact
        for fact in facts
        if fact.slot not in {"noise", "noise_date", "noise_amount", "noise_number", "counterparty_entity", "document_context"}
    ]
    if not summary.strip():
        status = "invalid"
    elif unresolved:
        status = "needs_agent"
    elif not meaningful:
        status = "invalid"
    else:
        status = "agent_complete" if agent_facts else "rule_complete"
    candidates = () if status == "needs_agent" else _candidate_ids(facts, rules)
    quality = _quality(facts, candidates, status, rules)
    spans = tuple(
        fact.public()
        for fact in sorted(facts, key=lambda fact: (fact.start, fact.end, fact.slot, fact.source))
    )
    return SummarySemanticResult(
        summary,
        status,
        spans,
        candidates,
        quality,
        _reason(status, candidates, quality),
        unresolved,
        unexplained,
    )


def analyze_summary(summary: str, rules: Mapping[str, object]) -> SummarySemanticResult:
    return _analyze(summary, rules)


def build_summary_agent_task(result: SummarySemanticResult) -> dict[str, object] | None:
    if result.status != "needs_agent":
        return None
    return {
        "task_id": "SUMMARY-" + hashlib.sha256(result.summary.encode("utf-8")).hexdigest()[:20],
        "summary": result.summary,
        "unresolved_slots": list(result.unresolved_slots),
        "unexplained_spans": [
            {"text": span.text, "start": span.start, "end": span.end}
            for span in result.unexplained_spans
        ],
        "allowed_slots": list(result.unresolved_slots),
        "allowed_outcomes": ["resolved", "source_insufficient"],
        "instruction": "只返回未解释原文中的受控语义槽位、枚举值和半开区间；确实无法从原文解释时返回source_insufficient；不得判断项目、质量、分数或动作。",
    }


def _allowed_values(rules: Mapping[str, object]) -> dict[str, set[str]]:
    allowed: dict[str, set[str]] = {
        "clause_binding": {"single_cash_leg", "multiple_cash_legs", "counterparty_action"}
    }
    for section in _LEXICON_SECTIONS:
        for entry in rules["lexicons"][section]:
            allowed.setdefault(entry["slot"], set()).add(entry["value"])
    return allowed


def merge_summary_agent_slots(
    result: SummarySemanticResult,
    payload: Mapping[str, object],
    rules: Mapping[str, object],
) -> SummarySemanticResult:
    forbidden = _FORBIDDEN_AGENT_FIELDS.intersection(payload)
    if forbidden:
        raise ValueError(f"Agent不得返回会计判断字段：{'、'.join(sorted(forbidden))}")
    if result.status != "needs_agent":
        raise ValueError("该摘要没有待Agent补充的语言槽位")
    if payload.get("summary", result.summary) != result.summary:
        raise ValueError("Agent结果摘要与当前任务不一致")
    outcome = payload.get("outcome", "resolved")
    if outcome not in {"resolved", "source_insufficient"}:
        raise ValueError("Agent结果outcome必须是resolved或source_insufficient")
    raw_spans = payload.get("spans")
    if outcome == "source_insufficient":
        if raw_spans not in (None, []):
            raise ValueError("原文无法进一步解释时不得同时返回语义槽位")
        return SummarySemanticResult(
            result.summary,
            "agent_insufficient",
            result.spans,
            (),
            EvidenceQuality.INVALID,
            "摘要Agent仍无法从原文解释剩余业务内容；摘要不参与候选和评分",
            result.unresolved_slots,
            result.unexplained_spans,
        )
    if not isinstance(raw_spans, list) or not raw_spans:
        raise ValueError("Agent结果必须包含非空spans")

    allowed = _allowed_values(rules)
    agent_facts: list[_Fact] = []
    for raw in raw_spans:
        if not isinstance(raw, Mapping):
            raise ValueError("Agent槽位必须是对象")
        extra = set(raw).difference({"slot", "value", "text", "start", "end"})
        if extra:
            raise ValueError(f"Agent槽位包含未允许字段：{'、'.join(sorted(extra))}")
        slot = raw.get("slot")
        value = raw.get("value")
        start = raw.get("start")
        end = raw.get("end")
        text = raw.get("text")
        if slot not in result.unresolved_slots or value not in allowed.get(slot, set()):
            raise ValueError("Agent返回了未授权槽位或枚举值")
        if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(result.summary):
            raise ValueError("Agent返回的原文区间非法")
        if result.summary[start:end] != text:
            raise ValueError("Agent返回的原文区间与摘要不一致")
        if not any(
            unresolved.start <= start and end <= unresolved.end
            for unresolved in result.unexplained_spans
        ):
            raise ValueError("Agent只能解释当前记录的未解释原文区间")
        agent_facts.append(_Fact(slot, value, text, start, end, "agent"))

    merged = _analyze(result.summary, rules, agent_facts)
    if merged.status != "agent_complete":
        raise ValueError("Agent补充后摘要语义仍未完成")
    return merged


def validate_summary_batch(
    results: Sequence[SummarySemanticResult],
    expected_summaries: Sequence[str],
) -> None:
    expected = tuple(expected_summaries)
    if len(results) != len(expected) or {result.summary for result in results} != set(expected):
        raise ValueError("摘要语义结果与当前任务不完整对应")
    if any(result.status == "needs_agent" for result in results):
        raise ValueError("摘要语义仍有未完成Agent任务")
    for result in results:
        if any(result.summary[span.start : span.end] != span.text for span in result.spans):
            raise ValueError("摘要语义存在与原文不一致的区间")
        if result.status in {"rule_complete", "agent_complete"} and result.unexplained_spans:
            raise ValueError("摘要语义标记完成但仍有未解释业务内容")
        if (
            result.status in {"rule_complete", "agent_complete"}
            and result.quality is EvidenceQuality.INVALID
            and not result.spans
        ):
            raise ValueError("整批摘要语义退化：完成状态没有形成有效语义证据")
        if (
            result.status == "agent_insufficient"
            and not result.unresolved_slots
            and not result.unexplained_spans
        ):
            raise ValueError("摘要原文不足终态必须保留未决语言槽位或未解释业务内容")
