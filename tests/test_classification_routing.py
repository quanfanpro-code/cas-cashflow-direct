from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path

import pytest

from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    MaterialityAmounts,
)
from cashflow_direct.account_dictionary import load_common_dictionary
from cashflow_direct.classification import classify_all as _classify_all, load_rule_pack
from cashflow_direct.summary_semantics import analyze_summary, load_summary_rules
from tests.fixture_factory import cashflow_component


THRESHOLDS = MaterialityAmounts(
    overall_cent=10_000,
    performance_cent=1_000,
    trivial_cent=100,
)


def route_classification_decisions(*args, **kwargs):
    module = importlib.import_module("cashflow_direct.classification")
    return module.route_classification_decisions(*args, **kwargs)


def classify_all(components, rules):
    root = Path(__file__).resolve().parents[1]
    semantics_rules = load_summary_rules(root)
    semantics = {
        component.summary: analyze_summary(component.summary, semantics_rules)
        for component in components
    }
    return _classify_all(
        components,
        rules,
        load_common_dictionary(root),
        semantics,
    )


def _decision(
    component_id: str,
    *,
    score: int | None,
    state: str = "agrees",
    source_conflict: bool = False,
    candidate: str = "CFO-04",
    business_object: str = "",
    purpose: str = "",
) -> ClassificationDecision:
    return ClassificationDecision(
        component_id=component_id,
        system_item_id=candidate,
        system_item_name="购买商品、接受劳务支付的现金" if candidate else "",
        normal_direction="outflow" if candidate else "net",
        matched_rule_id="TEST",
        reason="构造候选",
        evidence_level="high" if score in {70, 90} else "medium",
        resolved=False,
        evidence_score=score,
        evidence_sources=("summary", "account_path") if score in {70, 90} else ("summary",),
        candidate_item_ids=(candidate,) if candidate else (),
        original_item_state=state,
        source_conflict=source_conflict,
        business_object=business_object,
        purpose=purpose,
    )


def test_m1_agreement_with_score_55_is_decided_automatically() -> None:
    component = cashflow_component(
        "支付货款",
        -500,
        ("应付账款_供应商",),
        original_item_text="购买商品、接受劳务支付的现金",
        component_id="AUTO",
    )

    result = route_classification_decisions(
        (component,), (_decision("AUTO", score=55),), THRESHOLDS
    )

    assert result.decisions[0].resolved is True
    assert result.decisions[0].decision_action == "automatic_keep"
    assert result.ai_tasks == ()


def test_m1_original_conflict_with_score_90_still_requires_ai() -> None:
    component = cashflow_component(
        "税收滞纳金",
        -500,
        ("营业外支出_罚款滞纳金",),
        original_item_text="支付的各项税费",
        component_id="CONFLICT",
    )

    result = route_classification_decisions(
        (component,),
        (_decision("CONFLICT", score=90, state="conflicts", candidate="CFO-07"),),
        THRESHOLDS,
    )

    assert result.decisions[0].resolved is False
    assert result.decisions[0].decision_action == "ai_review"
    assert len(result.ai_tasks) == 1


def test_customer_threshold_55_allows_m0_score_55_change() -> None:
    component = cashflow_component(
        "税费付款",
        -50,
        ("应交税费_车船税",),
        original_item_text="支付其他与经营活动有关的现金",
        component_id="CUSTOM-THRESHOLD",
    )

    result = route_classification_decisions(
        (component,),
        (_decision("CUSTOM-THRESHOLD", score=55, state="conflicts", candidate="CFO-07"),),
        THRESHOLDS,
        automatic_change_threshold=55,
    )

    assert result.decisions[0].resolved is True
    assert result.decisions[0].decision_action == "automatic_change"


def test_same_source_business_conflict_is_blocked_before_ai_tasks() -> None:
    components = (
        CashflowComponent(
            component_id="FACT-CONFLICT-1",
            voucher_key="V-SAME",
            summary="同一事实",
            cash_delta_cent=-50,
            counterpart_accounts=("其他应付款_同一对象",),
            source_file_ids=("F-SAME",),
        ),
        CashflowComponent(
            component_id="FACT-CONFLICT-2",
            voucher_key="V-SAME",
            summary="同一事实",
            cash_delta_cent=-50,
            counterpart_accounts=("其他应付款_同一对象",),
            source_file_ids=("F-SAME",),
        ),
    )
    decisions = (
        _decision(
            "FACT-CONFLICT-1",
            score=70,
            state="blank",
            candidate="CFO-04",
        ),
        _decision(
            "FACT-CONFLICT-2",
            score=70,
            state="blank",
            candidate="CFO-07",
        ),
    )

    result = route_classification_decisions(components, decisions, THRESHOLDS)

    assert result.ai_tasks == ()
    assert all(item.business_conflict for item in result.decisions)
    assert {
        item.decision_action for item in result.decisions
    } == {"low_amount_human_batch"}


def test_automatic_keep_restores_original_item_in_the_result() -> None:
    component = cashflow_component(
        "模糊报销",
        -50,
        ("其他应付款_往来款",),
        original_item_text="支付其他与经营活动有关的现金",
        component_id="KEEP-ORIGINAL",
    )
    decision = replace(
        _decision(
            "KEEP-ORIGINAL",
            score=55,
            state="conflicts",
            candidate="CFO-04",
        ),
        original_standard_item_id="CFO-07",
    )

    result = route_classification_decisions((component,), (decision,), THRESHOLDS)

    kept = result.decisions[0]
    assert kept.decision_action == "automatic_keep"
    assert kept.system_item_id == "CFO-07"
    assert kept.system_item_name == "支付其他与经营活动有关的现金"


def test_small_refund_clue_keeps_the_valid_original_without_review() -> None:
    component = cashflow_component(
        "供应商退回采购款",
        50,
        ("应付账款_材料供应商",),
        original_item_text="购买商品、接受劳务支付的现金",
        component_id="REFUND",
    )

    result = route_classification_decisions(
        (component,), (_decision("REFUND", score=90),), THRESHOLDS
    )

    assert result.decisions[0].direction_status == "incompatible"
    assert result.decisions[0].resolved is True
    assert result.decisions[0].decision_action == "automatic_keep"
    assert result.ai_tasks == ()


def test_unknown_reverse_direction_also_keeps_a_valid_small_original() -> None:
    component = cashflow_component(
        "反向收款",
        50,
        ("应付账款_供应商",),
        original_item_text="购买商品、接受劳务支付的现金",
        component_id="UNKNOWN-REVERSAL",
    )

    result = route_classification_decisions(
        (component,), (_decision("UNKNOWN-REVERSAL", score=90),), THRESHOLDS
    )

    assert result.decisions[0].direction_status == "incompatible"
    assert result.decisions[0].resolved is True
    assert result.decisions[0].decision_action == "automatic_keep"
    assert result.ai_tasks == ()


def test_component_marker_cannot_create_a_special_refund_route() -> None:
    component = cashflow_component(
        "反向收款",
        50,
        ("应付账款_供应商",),
        original_item_text="购买商品、接受劳务支付的现金",
        anomalies=("历史退款标记",),
        component_id="FAKE-REVERSAL",
    )

    result = route_classification_decisions(
        (component,), (_decision("FAKE-REVERSAL", score=90),), THRESHOLDS
    )

    assert result.decisions[0].direction_status == "incompatible"
    assert result.decisions[0].decision_action == "automatic_keep"
    assert not hasattr(result.decisions[0], "退款审批规则")


def test_net_statement_item_missing_facts_does_not_reopen_valid_original() -> None:
    component = cashflow_component(
        "处置固定资产收款",
        50,
        ("固定资产清理_设备",),
        original_item_text="处置固定资产、无形资产和其他长期资产收回的现金净额",
        component_id="NET-ITEM",
    )
    decision = replace(
        _decision("NET-ITEM", score=90, candidate="CFI-03"),
        normal_direction="inflow",
    )

    result = route_classification_decisions((component,), (decision,), THRESHOLDS)

    assert result.decisions[0].resolved is True
    assert result.decisions[0].business_conflict is False
    assert result.decisions[0].net_item_facts_missing is True
    assert result.decisions[0].decision_action == "automatic_keep"
    assert "净额资料" in result.decisions[0].reason


def test_confirmed_netting_facts_allow_the_normal_route() -> None:
    component = cashflow_component(
        "处置固定资产收款",
        50,
        ("固定资产清理_设备",),
        original_item_text="处置固定资产、无形资产和其他长期资产收回的现金净额",
        component_id="NET-CONFIRMED",
    )
    decision = replace(
        _decision("NET-CONFIRMED", score=90, candidate="CFI-03"),
        normal_direction="inflow",
    )
    notes = ({
        "note_id": "NOTE-101",
        "状态": "仅本次采用",
        "规则类型": "净额项目资料确认",
        "适用摘要词": ["处置固定资产"],
        "内容": "已确认处置价款及全部相关处置费用，净额资料完整",
    },)

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS, company_notes=notes
    )

    assert result.decisions[0].business_conflict is False
    assert result.decisions[0].resolved is True


def test_m2_agreement_does_not_require_ai_to_prove_the_original_can_stay() -> None:
    component = cashflow_component(
        "支付货款",
        -1_000,
        ("应付账款_供应商",),
        original_item_text="购买商品、接受劳务支付的现金",
        component_id="DOUBLE",
    )

    result = route_classification_decisions(
        (component,), (_decision("DOUBLE", score=55),), THRESHOLDS
    )

    assert result.decisions[0].decision_action == "automatic_keep"
    assert result.decisions[0].resolved is True
    assert result.ai_tasks == ()


def test_same_class_items_keep_their_own_single_amount_levels() -> None:
    components = tuple(
        cashflow_component(
            "支付货款",
            -60,
            ("应付账款_供应商",),
            original_item_text="购买商品、接受劳务支付的现金",
            component_id=component_id,
        )
        for component_id in ("A", "B")
    )

    result = route_classification_decisions(
        components,
        tuple(
            _decision(
                component_id,
                score=45,
                business_object="采购商品",
                purpose="应付商品款",
            )
            for component_id in ("A", "B")
        ),
        THRESHOLDS,
    )

    assert {item.materiality_level for item in result.decisions} == {"M0"}
    assert all(not hasattr(item, "group_id") for item in result.decisions)
    assert {item.decision_action for item in result.decisions} == {"automatic_keep"}
    assert result.ai_tasks == ()


def test_three_performance_level_items_do_not_create_an_overall_gate() -> None:
    components = tuple(
        cashflow_component(
            "支付货款",
            -4_000,
            ("应付账款_供应商",),
            original_item_text="购买商品、接受劳务支付的现金",
            component_id=component_id,
        )
        for component_id in ("A", "B", "C")
    )
    decisions = tuple(
        _decision(
            component_id,
            score=55,
            business_object="采购商品",
            purpose="应付商品款",
        )
        for component_id in ("A", "B", "C")
    )

    result = route_classification_decisions(components, decisions, THRESHOLDS)

    assert {item.decision_action for item in result.decisions} == {"automatic_keep"}
    assert {item.single_materiality_level for item in result.decisions} == {"M2"}
    assert all(not hasattr(item, "group_id") for item in result.decisions)
    assert result.ai_tasks == ()


def test_repeated_items_have_no_separate_confirmation_state() -> None:
    components = tuple(
        cashflow_component(
            "支付货款",
            -4_000,
            ("应付账款_供应商",),
            original_item_text="购买商品、接受劳务支付的现金",
            component_id=component_id,
        )
        for component_id in ("A", "B", "C")
    )
    decisions = tuple(
        _decision(
            component_id,
            score=55,
            business_object="采购商品",
            purpose="应付商品款",
        )
        for component_id in ("A", "B", "C")
    )
    result = route_classification_decisions(
        components, decisions, THRESHOLDS
    )

    assert {item.decision_action for item in result.decisions} == {"automatic_keep"}
    assert {item.materiality_level for item in result.decisions} == {"M2"}
    assert all(not hasattr(item, "group_confirmation_status") for item in result.decisions)
    assert result.ai_tasks == ()


def test_repeated_uncertain_items_still_use_only_single_amount_level() -> None:
    components = tuple(
        cashflow_component(
            "支付往来款",
            -4_000,
            ("其他应付款",),
            original_item_text="购买商品、接受劳务支付的现金",
            component_id=component_id,
        )
        for component_id in ("A", "B", "C")
    )
    decisions = tuple(
        _decision(component_id, score=55, purpose="")
        for component_id in ("A", "B", "C")
    )

    result = route_classification_decisions(components, decisions, THRESHOLDS)

    assert {item.decision_action for item in result.decisions} == {"automatic_keep"}
    assert {item.materiality_level for item in result.decisions} == {"M2"}
    assert all(not hasattr(item, "grouping_status") for item in result.decisions)


@pytest.mark.parametrize(
    "purpose",
    ("其他外部往来款", "其他收益", "进项税额", "共用进项税额"),
)
def test_broad_or_vat_purpose_never_changes_single_amount_level(purpose: str) -> None:
    components = tuple(
        cashflow_component(
            "构造摘要",
            -4_000,
            (f"其他应付款_{purpose}",),
            original_item_text="购买商品、接受劳务支付的现金",
            component_id=component_id,
        )
        for component_id in ("A", "B", "C")
    )
    decisions = tuple(
        _decision(component_id, score=55, purpose=purpose)
        for component_id in ("A", "B", "C")
    )

    result = route_classification_decisions(components, decisions, THRESHOLDS)

    assert {item.materiality_level for item in result.decisions} == {"M2"}
    assert all(not hasattr(item, "grouping_status") for item in result.decisions)


def test_single_m3_still_requires_individual_human_decision() -> None:
    component = cashflow_component(
        "支付重大设备款",
        -10_000,
        ("在建工程_设备",),
        original_item_text="购建固定资产、无形资产和其他长期资产支付的现金",
        component_id="SINGLE-M3",
    )
    decision = _decision("SINGLE-M3", score=90, candidate="CFI-06")

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS
    )

    assert result.decisions[0].decision_action == "human_decision"
    assert result.decisions[0].single_materiality_level == "M3"


def test_different_business_objects_and_purposes_are_not_combined() -> None:
    components = tuple(
        cashflow_component(
            "支付经营费用",
            -60,
            ("管理费用_经营费用",),
            original_item_text="购买商品、接受劳务支付的现金",
            component_id=component_id,
        )
        for component_id in ("TRAVEL", "REPAIR")
    )
    decisions = (
        _decision(
            "TRAVEL",
            score=45,
            business_object="员工差旅",
            purpose="日常出差",
        ),
        _decision(
            "REPAIR",
            score=45,
            business_object="设备维修",
            purpose="维持生产",
        ),
    )

    result = route_classification_decisions(components, decisions, THRESHOLDS)

    assert {item.materiality_level for item in result.decisions} == {"M0"}
    assert {item.decision_action for item in result.decisions} == {"automatic_keep"}


def test_same_purpose_with_different_business_objects_stays_per_item() -> None:
    components = tuple(
        cashflow_component(
            "支付日常维护款",
            -60,
            ("管理费用_日常维护",),
            original_item_text="支付其他与经营活动有关的现金",
            component_id=component_id,
        )
        for component_id in ("OFFICE", "EQUIPMENT")
    )
    decisions = (
        _decision(
            "OFFICE",
            score=45,
            candidate="CFO-07",
            business_object="办公场所",
            purpose="日常维护",
        ),
        _decision(
            "EQUIPMENT",
            score=45,
            candidate="CFO-07",
            business_object="生产设备",
            purpose="日常维护",
        ),
    )

    result = route_classification_decisions(components, decisions, THRESHOLDS)

    assert {item.materiality_level for item in result.decisions} == {"M0"}
    assert all(not hasattr(item, "group_id") for item in result.decisions)


def test_source_conflict_keeps_valid_original_while_illegal_input_is_isolated() -> None:
    conflict = cashflow_component(
        "发放工资",
        -500,
        ("在建工程_工资",),
        original_item_text="购买商品、接受劳务支付的现金",
        component_id="SOURCE-CONFLICT",
    )
    illegal = cashflow_component(
        "",
        -500,
        ("应付账款_供应商",),
        component_id="ILLEGAL",
    )
    decisions = (
        _decision(
            "SOURCE-CONFLICT",
            score=None,
            state="conflicts",
            source_conflict=True,
        ),
        _decision("ILLEGAL", score=0, state="pending_comparison", candidate=""),
    )

    result = route_classification_decisions(
        (conflict, illegal), decisions, THRESHOLDS
    )

    by_id = {item.component_id: item for item in result.decisions}
    assert by_id["SOURCE-CONFLICT"].decision_action == "automatic_keep"
    assert by_id["SOURCE-CONFLICT"].resolved is True
    assert by_id["ILLEGAL"].decision_action == "isolate_invalid_input"
    assert by_id["ILLEGAL"].resolved is False


def test_direction_clue_cannot_force_review_of_a_valid_original() -> None:
    component = cashflow_component(
        "支付设备维修款",
        -500,
        ("管理费用_维修费",),
        component_id="DIRECTION",
    )
    decision = replace(
        _decision("DIRECTION", score=45, candidate="CFO-01"),
        normal_direction="inflow",
    )

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS
    )

    assert result.decisions[0].decision_action == "automatic_keep"
    assert result.decisions[0].direction_status == "incompatible"
    assert result.ai_tasks == ()


def test_explicit_exclusion_bypasses_the_scoring_matrix() -> None:
    component = cashflow_component(
        "现金范围内部划转",
        100,
        ("银行存款_一般户",),
        component_id="EXCLUDED",
        anomalies=("internal_transfer",),
    )
    decision = ClassificationDecision(
        component_id="EXCLUDED",
        system_item_id="",
        system_item_name="",
        normal_direction="net",
        matched_rule_id="EXCLUDED",
        reason="现金范围内部划转不进入正表",
        evidence_level="not_applicable",
        resolved=True,
        excluded=True,
        evidence_score=None,
        decision_action="exclude",
    )

    result = route_classification_decisions((component,), (decision,), THRESHOLDS)

    assert result.decisions == (decision,)
    assert result.ai_tasks == ()


def test_unknown_service_individual_tax_keeps_a_standardized_original_below_overall() -> None:
    rules = load_rule_pack(Path(__file__).resolve().parents[1])
    trivial = cashflow_component(
        "代扣个人所得税",
        -50,
        ("应交税费_个人所得税",),
        original_item_text="支付的各项税费",
        component_id="TAX-TRIVIAL",
    )
    below_performance = cashflow_component(
        "代扣个人所得税",
        -500,
        ("应交税费_个人所得税",),
        original_item_text="支付的各项税费",
        component_id="TAX-BELOW-PERFORMANCE",
    )
    trivial_result = route_classification_decisions(
        (trivial,), classify_all((trivial,), rules), THRESHOLDS
    )
    below_performance_result = route_classification_decisions(
        (below_performance,), classify_all((below_performance,), rules), THRESHOLDS
    )
    by_id = {
        trivial_result.decisions[0].component_id: trivial_result.decisions[0],
        below_performance_result.decisions[0].component_id: below_performance_result.decisions[0],
    }

    assert by_id["TAX-TRIVIAL"].resolved is True
    assert by_id["TAX-TRIVIAL"].decision_action == "automatic_keep"
    assert by_id["TAX-BELOW-PERFORMANCE"].resolved is True
    assert by_id["TAX-BELOW-PERFORMANCE"].decision_action == "automatic_keep"
    assert trivial_result.ai_tasks == ()
    assert below_performance_result.ai_tasks == ()


def test_unknown_service_individual_tax_with_blank_original_uses_batch_then_blind_ai() -> None:
    rules = load_rule_pack(Path(__file__).resolve().parents[1])
    low = cashflow_component(
        "代扣个人所得税",
        -50,
        ("应交税费_个人所得税",),
        original_item_text="",
        component_id="TAX-BLANK-M0",
    )
    below_performance = cashflow_component(
        "代扣个人所得税",
        -500,
        ("应交税费_个人所得税",),
        original_item_text="",
        component_id="TAX-BLANK-M1",
    )
    performance = cashflow_component(
        "代扣个人所得税",
        -5_000,
        ("应交税费_个人所得税",),
        original_item_text="",
        component_id="TAX-BLANK-M2",
    )

    result = route_classification_decisions(
        (low, below_performance, performance),
        classify_all((low, below_performance, performance), rules),
        THRESHOLDS,
    )
    by_id = {item.component_id: item for item in result.decisions}

    assert by_id["TAX-BLANK-M0"].decision_action == "low_amount_human_batch"
    assert by_id["TAX-BLANK-M1"].decision_action == "human_batch"
    assert by_id["TAX-BLANK-M2"].decision_action == "double_ai_review"
    assert by_id["TAX-BLANK-M2"].ai_review_policy == "individual_tax_service"
    assert len(result.ai_tasks) == 2


def test_two_applicable_company_rules_with_different_outcomes_go_to_human() -> None:
    component = cashflow_component(
        "支付设备押金",
        -50,
        ("其他应付款_设备押金",),
        original_item_text="支付其他与经营活动有关的现金",
        component_id="NOTE-CONFLICT",
    )
    decision = _decision(
        "NOTE-CONFLICT",
        score=70,
        state="agrees",
        candidate="CFO-07",
    )
    notes = (
        {
            "note_id": "NOTE-01",
            "内容": "设备押金按经营活动",
            "涉及科目或词": ["设备押金"],
            "建议处理": "CFO-07",
            "状态": "采用",
        },
        {
            "note_id": "NOTE-02",
            "内容": "设备押金按投资活动",
            "涉及科目或词": ["设备押金"],
            "建议处理": "CFI-06",
            "状态": "采用",
        },
    )

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS, company_notes=notes
    )

    assert result.decisions[0].decision_action == "human_decision"
    assert result.decisions[0].company_rule_conflict is True
    assert result.ai_tasks == ()


@pytest.mark.parametrize(
    "note",
    (
        {
            "note_id": "NOTE-EXPIRED",
            "内容": "设备押金按投资活动",
            "涉及科目或词": ["设备押金"],
            "建议处理": "CFI-06",
            "状态": "已停用",
        },
        {
            "note_id": "NOTE-OUT-OF-SCOPE",
            "内容": "设备押金按投资活动",
            "涉及科目或词": ["设备押金"],
            "适用标准一级科目": ["长期股权投资"],
            "建议处理": "CFI-06",
            "状态": "采用",
        },
    ),
)
def test_expired_or_out_of_scope_company_rule_requires_human_decision(note) -> None:
    component = cashflow_component(
        "支付设备押金",
        -50,
        ("其他应付款_设备押金",),
        original_item_text="支付其他与经营活动有关的现金",
        component_id="NOTE-INVALID-SCOPE",
    )
    decision = _decision(
        "NOTE-INVALID-SCOPE",
        score=70,
        state="agrees",
        candidate="CFO-07",
    )

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS, company_notes=(note,)
    )

    assert result.decisions[0].decision_action == "human_decision"
    assert result.ai_tasks == ()


def test_vat_without_a_base_transaction_cannot_reclassify_a_valid_original() -> None:
    component = cashflow_component(
        "水费发票",
        -500,
        ("应交税费_应交增值税_进项税",),
        original_item_text="支付其他与经营活动有关的现金",
        component_id="VAT-NO-BASE",
    )
    decision = replace(
        _decision(
            "VAT-NO-BASE",
            score=90,
            state="conflicts",
            candidate="CFO-04",
        ),
        original_standard_item_id="CFO-07",
    )

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS
    )

    assert result.decisions[0].decision_action == "automatic_keep"
    assert result.decisions[0].system_item_id == "CFO-07"
    assert result.decisions[0].vat_base_missing is True
    assert result.ai_tasks == ()


def test_overall_material_refund_clue_uses_the_normal_human_route() -> None:
    component = cashflow_component(
        "供应商退回设备款",
        20_000,
        ("应付账款_应付设备款",),
        original_item_text="",
        component_id="NEW-REVERSAL-M3",
    )
    decision = _decision(
        "NEW-REVERSAL-M3",
        score=70,
        state="blank",
        candidate="CFI-06",
    )
    decision = replace(decision, normal_direction="outflow")

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS
    )

    assert result.decisions[0].decision_action == "human_decision"
    assert not hasattr(result.decisions[0], "refund_pattern")
    assert result.ai_tasks == ()


def test_small_returned_fee_uses_the_normal_blank_item_route() -> None:
    component = cashflow_component(
        "退还手续费",
        50,
        ("财务费用_手续费",),
        original_item_text="",
        component_id="RETURNED-FEE-M0",
    )
    decision = replace(
        _decision(
            "RETURNED-FEE-M0",
            score=70,
            state="blank",
            candidate="CFO-07",
        ),
        normal_direction="outflow",
    )

    result = route_classification_decisions(
        (component,), (decision,), THRESHOLDS
    )

    assert result.decisions[0].decision_action == "automatic_fill"
    assert not hasattr(result.decisions[0], "refund_pattern")
    assert result.ai_tasks == ()
