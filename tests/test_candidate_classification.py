from __future__ import annotations

import unittest
from dataclasses import dataclass
from pathlib import Path

from cashflow_direct.account_dictionary import (
    AccountDictionary,
    AccountSemanticEntry,
    load_common_dictionary,
)
from cashflow_direct.classification import (
    classify_component as _classify_component,
    load_rule_pack,
)
from cashflow_direct.decision_policy import EvidenceQuality
from cashflow_direct.summary_semantics import (
    SummarySemanticResult,
    SummarySpan,
    analyze_summary,
    load_summary_rules,
)
from tests.fixture_factory import cashflow_component


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SummarySemanticEntry:
    """旧测试数据的最小承载体；进入分类前一律转换为正式摘要语义结果。"""

    summary: str
    semantic: str
    item_id: str
    basis: str
    confidence: str
    classification_facts: tuple[str, ...]
    candidate_item_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class SummaryDictionary:
    entries: tuple[SummarySemanticEntry, ...]


def _fixture_semantics(fixtures: SummaryDictionary) -> dict[str, SummarySemanticResult]:
    quality_by_name = {
        "invalid": EvidenceQuality.INVALID,
        "low": EvidenceQuality.WEAK,
        "weak": EvidenceQuality.WEAK,
        "medium": EvidenceQuality.MEDIUM,
        "high": EvidenceQuality.STRONG,
    }
    results: dict[str, SummarySemanticResult] = {}
    for entry in fixtures.entries:
        spans = tuple(
            SummarySpan(
                fact.split(":", 1)[0],
                fact.split(":", 1)[-1],
                0,
                len(entry.summary),
                "test_fixture",
            )
            for fact in entry.classification_facts
        )
        candidates = entry.candidate_item_ids or ((entry.item_id,) if entry.item_id else ())
        results[entry.summary] = SummarySemanticResult(
            entry.summary,
            "complete",
            spans,
            candidates,
            quality_by_name[entry.confidence],
            entry.basis,
        )
    return results


def classify_component(
    component,
    rules,
    dictionary=None,
    summary_semantics=None,
    *,
    summary_dictionary=None,
):
    """测试也必须通过正式摘要语义边界，不允许旧关键词回退。"""
    if summary_dictionary is not None:
        summary_semantics = summary_dictionary
    if isinstance(summary_semantics, SummaryDictionary):
        semantics = _fixture_semantics(summary_semantics)
        if component.summary not in semantics:
            semantics[component.summary] = analyze_summary(
                component.summary, load_summary_rules(ROOT)
            )
    elif summary_semantics is None:
        semantics = {
            component.summary: analyze_summary(component.summary, load_summary_rules(ROOT))
        }
    else:
        semantics = summary_semantics
    account_dictionary = dictionary or load_common_dictionary(ROOT)
    return _classify_component(component, rules, account_dictionary, semantics)


class FormalSummarySemanticsTests(unittest.TestCase):
    def test_classification_requires_formal_summary_semantics_and_never_falls_back(self) -> None:
        component = cashflow_component(
            "支付工资",
            -100,
            original_item_text="支付给职工以及为职工支付的现金",
        )

        with self.assertRaisesRegex(RuntimeError, "摘要语义尚未完成"):
            _classify_component(
                component,
                load_rule_pack(ROOT),
                summary_semantics=None,
            )

    def test_formal_summary_semantics_is_the_only_summary_source(self) -> None:
        component = cashflow_component("缴纳税款", -100)
        semantic = analyze_summary(component.summary, load_summary_rules(ROOT))

        decision = classify_component(
            component,
            load_rule_pack(ROOT),
            summary_semantics={component.summary: semantic},
        )

        self.assertEqual(("CFO-06",), decision.summary_candidate_item_ids)
        self.assertEqual(25, decision.summary_quality)


def test_original_label_alone_is_not_a_candidate_or_evidence() -> None:
    component = cashflow_component(
        "转账",
        -100,
        (),
        original_item_text="购买商品、接受劳务支付的现金",
    )

    decision = classify_component(component, load_rule_pack(ROOT))

    assert decision.system_item_id == ""
    assert decision.candidate_item_ids == ()
    assert decision.evidence_score == 0
    assert decision.evidence_sources == ()
    assert decision.original_item_state == "pending_comparison"
    assert decision.candidate_status == "no_candidate"
    assert decision.original_standard_item_id == "CFO-04"
    assert decision.resolved is False


def test_complete_path_dictionary_can_create_candidate_without_keyword_rule() -> None:
    component = cashflow_component(
        "付彭娟报销停车费",
        -3_100,
        ("管理费用_交通费_车辆费用_停车费",),
        original_item_text="支付其他与经营活动有关的现金",
    )
    dictionary = AccountDictionary((
        AccountSemanticEntry(
            "管理费用_交通费_车辆费用_停车费",
            "日常经营车辆停车支出",
            "CFO-07",
            "完整路径显示日常经营管理费用",
            "high",
            "custom",
        ),
    ))

    decision = classify_component(component, load_rule_pack(ROOT), dictionary)

    assert decision.system_item_id == "CFO-07"
    assert decision.evidence_score == 45
    assert decision.account_path_quality == 45
    assert decision.original_item_state == "agrees"


def test_complete_path_semantics_survive_when_hard_rule_wins_same_candidate() -> None:
    component = cashflow_component(
        "发放工资",
        -100,
        ("应付职工薪酬_应付工资",),
        original_item_text="支付给职工以及为职工支付的现金",
    )
    dictionary = AccountDictionary((
        AccountSemanticEntry(
            "应付职工薪酬_应付工资",
            "支付职工工资",
            "CFO-05",
            "完整路径显示为应付职工薪酬下的工资",
            "high",
            "custom",
        ),
    ))

    decision = classify_component(component, load_rule_pack(ROOT), dictionary)

    assert decision.system_item_id == "CFO-05"
    assert "支付职工工资" in decision.business_object
    assert decision.purpose == "应付工资"


def test_exact_summary_semantics_create_candidate_without_keyword_classification() -> None:
    component = cashflow_component(
        "陈鑫焱住宿费专票 25652000000052744521",
        -11_907,
        ("应交税费_应交增值税_硬件进项税额",),
        original_item_text="支付其他与经营活动有关的现金",
    )
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            summary=component.summary,
            semantic="员工差旅住宿支出",
            item_id="CFO-07",
            basis="摘要原文显示住宿费专票",
            confidence="medium",
            classification_facts=("object:住宿费", "purpose:员工差旅"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        summary_dictionary=summary_dictionary,
    )

    assert decision.system_item_id == "CFO-07"
    assert decision.summary_quality == 25
    assert decision.evidence_score == 25
    assert decision.candidate_status == "available"


def test_summary_repeating_path_semantics_counts_as_one_source() -> None:
    component = cashflow_component(
        "支付工资",
        -100,
        ("应付职工薪酬_应付工资",),
        original_item_text="支付给职工以及为职工支付的现金",
    )
    facts = ("object:工资", "purpose:职工薪酬")
    account_dictionary = AccountDictionary((
        AccountSemanticEntry(
            "应付职工薪酬_应付工资",
            "支付职工工资",
            "CFO-05",
            "完整路径显示应付工资",
            "high",
            "custom",
            classification_facts=facts,
        ),
    ))
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "支付职工工资",
            "CFO-05",
            "摘要原文显示支付工资",
            "high",
            facts,
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        account_dictionary,
        summary_dictionary,
    )

    assert decision.evidence_score == 45
    assert decision.sources_independent is False


def test_structured_semantics_suppress_legacy_keyword_candidate() -> None:
    component = cashflow_component(
        "任映燃火车票进项",
        -100,
        ("应交税费_应交增值税_进项税",),
        original_item_text="支付其他与经营活动有关的现金",
    )
    account_dictionary = AccountDictionary((
        AccountSemanticEntry(
            "应交税费_应交增值税_进项税",
            "进项税须随具体交易性质判断",
            "",
            "完整路径本身不能唯一判断项目",
            "low",
            "custom",
        ),
    ))
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "员工差旅交通支出",
            "CFO-07",
            "摘要原文显示火车票",
            "medium",
            ("item:CFO-07", "object:差旅交通"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        account_dictionary,
        summary_dictionary,
    )

    assert decision.system_item_id == "CFO-07"
    assert decision.source_conflict is False
    assert decision.evidence_score == 25


def test_unrelated_non_vat_accounts_cannot_inherit_one_anothers_candidate() -> None:
    component = cashflow_component(
        "付员工报销款",
        -100,
        ("管理费用_职工薪酬_福利费", "其他应付款_其他外部往来款"),
        original_item_text="支付其他与经营活动有关的现金",
    )
    dictionary = AccountDictionary((
        AccountSemanticEntry(
            "管理费用_职工薪酬_福利费",
            "职工福利",
            "CFO-05",
            "完整路径显示职工福利",
            "high",
            "runtime",
            classification_facts=("object:employee_welfare",),
        ),
        AccountSemanticEntry(
            "其他应付款_其他外部往来款",
            "其他经营往来",
            "CFO-07",
            "完整路径显示其他经营往来",
            "high",
            "runtime",
            classification_facts=("object:other_payable",),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        dictionary,
        SummaryDictionary(()),
    )

    assert decision.business_conflict is True
    assert "多个非增值税业务" in decision.reason


def test_formal_semantics_suppress_legacy_keywords_without_custom_path_entries() -> None:
    component = cashflow_component(
        "支付供应商日常服务费",
        -100,
        ("应交税费_应交增值税_进项税",),
        original_item_text="支付其他与经营活动有关的现金",
    )
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "支付日常经营服务费",
            "CFO-07",
            "摘要原文显示支付供应商日常服务费",
            "high",
            ("object:日常服务", "purpose:经营管理"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        load_common_dictionary(ROOT),
        summary_dictionary,
    )

    assert decision.system_item_id == "CFO-07"
    assert decision.source_conflict is False
    assert decision.evidence_score == 45


def test_ambiguous_weak_summary_is_narrowed_by_exact_strong_path() -> None:
    component = cashflow_component(
        "付款",
        -100,
        ("应付账款_应付商品款",),
        original_item_text="购买商品、接受劳务支付的现金",
    )
    account_dictionary = AccountDictionary((
        AccountSemanticEntry(
            "应付账款_应付商品款",
            "购买商品或接受劳务形成的结算",
            "CFO-04",
            "完整路径明确显示应付商品款",
            "high",
            "custom",
            classification_facts=("item:CFO-04", "object:商品采购"),
        ),
    ))
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "只有付款动作，不能区分采购、费用或购建资产",
            "",
            "摘要原文只有“付款”",
            "low",
            ("action:付款",),
            candidate_item_ids=("CFO-04", "CFO-07", "CFI-06"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        account_dictionary,
        summary_dictionary,
    )

    assert decision.system_item_id == "CFO-04"
    assert decision.source_conflict is False
    assert decision.evidence_score == 55


def test_weak_multi_candidate_source_stays_ambiguous_without_narrowing_evidence() -> None:
    component = cashflow_component(
        "付款",
        -100,
        ("其他应付款_待查",),
        original_item_text="支付其他与经营活动有关的现金",
    )
    account_dictionary = AccountDictionary((
        AccountSemanticEntry(
            "其他应付款_待查",
            "完整路径不能判断具体业务",
            "",
            "完整路径只有待查往来性质",
            "low",
            "custom",
        ),
    ))
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "只有付款动作，不能区分采购、费用或购建资产",
            "",
            "摘要原文只有“付款”",
            "low",
            ("action:付款",),
            candidate_item_ids=("CFO-04", "CFO-07", "CFI-06"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        account_dictionary,
        summary_dictionary,
    )

    assert decision.system_item_id == ""
    assert decision.candidate_item_ids == ("CFO-04", "CFO-07", "CFI-06")
    assert decision.evidence_score == 10
    assert decision.source_conflict is False
    assert decision.candidate_status == "ambiguous"


def test_direction_specific_weak_path_candidates_accept_specific_summary() -> None:
    component = cashflow_component(
        "代员工支付社保",
        -100,
        ("其他应收款_关联方",),
        original_item_text="支付给职工以及为职工支付的现金",
    )
    account_dictionary = AccountDictionary((
        AccountSemanticEntry(
            "其他应收款_关联方",
            "关联方代垫或往来，具体项目须结合业务判断",
            "",
            "完整路径显示关联方往来，但不能唯一判断用途",
            "low",
            "custom",
            classification_facts=("account_context:其他应收款_关联方",),
            outflow_candidate_item_ids=("CFO-05", "CFO-07"),
            inflow_candidate_item_ids=("CFO-03",),
        ),
    ))
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "为员工支付社会保险",
            "CFO-05",
            "摘要原文明确表示代员工支付社保",
            "medium",
            ("object:员工", "purpose:社会保险"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        account_dictionary,
        summary_dictionary,
    )

    assert decision.system_item_id == "CFO-05"
    assert decision.source_conflict is False
    assert decision.evidence_score == 35


def test_disjoint_multi_candidate_summary_and_path_are_reported_as_conflict() -> None:
    component = cashflow_component(
        "付款",
        -100,
        ("在建工程_设备",),
    )
    account_dictionary = AccountDictionary((
        AccountSemanticEntry(
            "在建工程_设备",
            "购建长期资产",
            "CFI-06",
            "完整路径显示在建工程设备但信息质量仅按弱档测试",
            "low",
            "custom",
            classification_facts=("object:长期资产",),
        ),
    ))
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "只支持一般经营性付款候选",
            "",
            "摘要原文只有“付款”",
            "low",
            ("action:经营付款",),
            candidate_item_ids=("CFO-04", "CFO-07"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        account_dictionary,
        summary_dictionary,
    )

    assert decision.source_conflict is True
    assert decision.evidence_score is None
    assert set(decision.candidate_item_ids) == {"CFO-04", "CFO-07", "CFI-06"}


def test_two_weak_candidate_sets_can_intersect_at_one_unique_item() -> None:
    component = cashflow_component("付款", -100, ("应付账款_待查",))
    account_dictionary = AccountDictionary((
        AccountSemanticEntry(
            "应付账款_待查",
            "可能是商品采购或设备购建",
            "",
            "完整路径只能缩小到两个候选",
            "low",
            "custom",
            classification_facts=("account_context:应付账款",),
            outflow_candidate_item_ids=("CFO-04", "CFI-06"),
        ),
    ))
    summary_dictionary = SummaryDictionary((
        SummarySemanticEntry(
            component.summary,
            "可能是商品采购或其他经营付款",
            "",
            "摘要原文只有“付款”",
            "low",
            ("action:付款",),
            candidate_item_ids=("CFO-04", "CFO-07"),
        ),
    ))

    decision = classify_component(
        component,
        load_rule_pack(ROOT),
        account_dictionary,
        summary_dictionary,
    )

    assert decision.system_item_id == "CFO-04"
    assert decision.evidence_score == 20
    assert decision.source_conflict is False


def test_classification_produces_a_candidate_before_any_final_action() -> None:
    component = cashflow_component(
        "销售商品收到货款",
        100,
        ("合同负债_销售",),
        original_item_text="销售商品、提供劳务收到的现金",
    )

    decision = classify_component(component, load_rule_pack(ROOT))

    assert decision.system_item_id == "CFO-01"
    assert decision.candidate_item_ids == ("CFO-01",)
    assert decision.original_item_state == "agrees"
    assert decision.evidence_score == 70
    assert decision.summary_quality == 45
    assert decision.account_path_quality == 25
    assert decision.sources_independent is True
    assert decision.resolved is False


def test_original_item_conflict_is_recorded_without_classification_auto_change() -> None:
    component = cashflow_component(
        "税收滞纳金",
        -100,
        ("营业外支出_罚款、滞纳金",),
        original_item_text="支付的各项税费",
    )

    decision = classify_component(component, load_rule_pack(ROOT))

    assert decision.system_item_id == "CFO-07"
    assert decision.original_item_state == "conflicts"
    assert decision.evidence_score == 55
    assert decision.sources_independent is True
    assert decision.resolved is False
    assert decision.decision_source == "candidate"


def test_summary_and_path_conflict_have_no_usable_score() -> None:
    component = cashflow_component(
        "发放工资",
        -100,
        ("在建工程_工资",),
        original_item_text="支付给职工以及为职工支付的现金",
    )

    decision = classify_component(component, load_rule_pack(ROOT))

    assert decision.source_conflict is True
    assert decision.evidence_score is None
    assert set(decision.candidate_item_ids) == {"CFO-05", "CFI-06"}
    assert decision.resolved is False


def test_illegal_input_is_isolated_before_candidate_generation() -> None:
    component = cashflow_component(
        "",
        -100,
        ("应付账款_供应商",),
        original_item_text="购买商品、接受劳务支付的现金",
    )

    decision = classify_component(component, load_rule_pack(ROOT))

    assert decision.system_item_id == ""
    assert decision.candidate_item_ids == ()
    assert decision.decision_action == "isolate_invalid_input"
    assert decision.resolved is False
