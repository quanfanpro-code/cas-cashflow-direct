# -*- coding: utf-8 -*-
"""科目语义词典模块测试（Task 4）。"""
from __future__ import annotations

from pathlib import Path

import pytest

from cashflow_direct.account_dictionary import (
    AccountDictionary,
    AccountSemanticEntry,
    analyze_account_path,
    collect_detail_segments,
    load_common_dictionary,
    load_account_semantic_rules,
    merge_dictionaries,
    merge_account_agent_concepts,
    refuses_general_semantic_judgment,
    path_basis_is_traceable,
    score_dictionary_hits,
)
from cashflow_direct.models import CashflowComponent

ROOT = Path(__file__).resolve().parents[1]


def _component(accounts=(), cash_delta_cent=-100) -> CashflowComponent:
    return CashflowComponent(
        component_id="CMP-1", voucher_key="K", summary="", cash_delta_cent=cash_delta_cent,
        counterpart_accounts=accounts,
    )


def test_common_dictionary_loads_and_lookup():
    dictionary = load_common_dictionary(ROOT)
    entry = dictionary.lookup_path("在建工程_应付设备款")
    assert entry is not None and entry.outflow_item_id == "CFI-06" and entry.layer == "common"
    assert dictionary.lookup_path("管理费用_应付设备款").outflow_item_id != "CFI-06"
    assert dictionary.lookup("不存在的科目段") is None


def test_custom_overrides_common():
    common = AccountDictionary((AccountSemanticEntry("应付设备款", "购建", "CFI-06", "依据", "high", "common"),))
    custom = AccountDictionary((AccountSemanticEntry("应付设备款", "特殊业务", "CFI-09", "依据", "high", "custom"),))
    merged = merge_dictionaries(common, custom)
    assert merged.lookup("应付设备款").item_id == "CFI-09"


def test_runtime_full_path_cannot_override_exact_builtin_common_semantic() -> None:
    common = AccountDictionary((
        AccountSemanticEntry(
            "应付账款_应付设备款_往来款", "购建设备", "CFI-06", "准则通用语义", "high", "common"
        ),
    ))
    runtime = AccountDictionary((
        AccountSemanticEntry(
            "应付账款_应付设备款_往来款",
            "临时解释",
            "CFO-02",
            "运行时解释",
            "high",
            "runtime",
        ),
    ))

    hit = merge_dictionaries(common, runtime).lookup_path(
        "应付账款_应付设备款_往来款"
    )

    assert hit is not None
    assert hit.item_id == "CFI-06"
    assert hit.layer == "common"


def test_dictionary_does_not_fall_back_to_a_single_detail_segment() -> None:
    dictionary = AccountDictionary((
        AccountSemanticEntry("设备款", "设备", "CFI-06", "旧单段规则", "high", "common"),
    ))

    assert dictionary.lookup_path("管理费用_设备款") is None


def test_collect_detail_segments():
    assert collect_detail_segments(["应付账款_应付设备款_往来款", "库存现金", "应交税费_进项税"]) == ("应付设备款", "往来款", "进项税")


def test_score_dictionary_hits_levels_and_confidence():
    dictionary = merge_dictionaries(
        load_common_dictionary(ROOT),
        AccountDictionary((
            AccountSemanticEntry("其他应付款_回购义务", "筹资", "CFF-06", "依据", "high", "custom"),
            AccountSemanticEntry("应交税费_待抵扣税金", "不确定", "CFO-06", "依据", "medium", "custom"),
            AccountSemanticEntry("应交税费_低置信段", "不确定", "CFO-07", "依据", "low", "custom"),
            AccountSemanticEntry("应交税费_进项税", "随交易", "", "依据", "high", "custom"),
        )),
    )
    common_hits = score_dictionary_hits(_component(("在建工程_应付设备款",)), dictionary)
    assert common_hits[0].score == 45 and common_hits[0].item_id == "CFI-06"
    custom_hits = score_dictionary_hits(_component(("其他应付款_回购义务",)), dictionary)
    assert custom_hits[0].score == 45 and custom_hits[0].item_id == "CFF-06"
    medium_hits = score_dictionary_hits(_component(("应交税费_待抵扣税金",)), dictionary)
    assert medium_hits[0].score == 25
    low_hits = score_dictionary_hits(_component(("应交税费_低置信段",)), dictionary)
    assert low_hits[0].score == 10
    assert score_dictionary_hits(_component(("应交税费_进项税",)), dictionary) == ()  # item_id 为空不得分


def test_management_equipment_does_not_use_equipment_segment_as_long_asset() -> None:
    rules = load_account_semantic_rules(ROOT)
    result = analyze_account_path("管理费用_设备款", rules)

    assert "CFI-06" not in result.candidate_item_ids
    assert "CFI-06" not in result.outflow_candidate_item_ids


def test_production_labor_welfare_is_not_purchase_goods() -> None:
    rules = load_account_semantic_rules(ROOT)
    result = analyze_account_path("生产成本_鉴定成本_人工_福利", rules)

    assert result.outflow_candidate_item_ids == ("CFO-05",)
    assert "CFO-04" not in result.candidate_item_ids


def test_same_equipment_word_changes_with_parent_path() -> None:
    rules = load_account_semantic_rules(ROOT)

    assert analyze_account_path("固定资产_运输设备", rules).outflow_candidate_item_ids == ("CFI-06",)
    assert analyze_account_path("管理费用_办公设备维修", rules).outflow_candidate_item_ids == ("CFO-07",)
    assert not analyze_account_path("生产成本_设备折旧", rules).candidate_item_ids
    assert not analyze_account_path("生产成本_设备折旧", rules).outflow_candidate_item_ids


def test_account_agent_cannot_return_item_or_confidence() -> None:
    rules = load_account_semantic_rules(ROOT)
    result = analyze_account_path("其他应付款_客户特殊款", rules)

    with pytest.raises(ValueError, match="不得返回项目、质量或分数"):
        merge_account_agent_concepts(
            result,
            {"item_id": "CFI-06", "confidence": "high"},
            rules,
        )


def test_account_agent_can_only_fill_an_unresolved_path_node() -> None:
    rules = load_account_semantic_rules(ROOT)
    result = analyze_account_path("其他应付款_客户特殊款", rules)

    with pytest.raises(ValueError, match="只能补未识别节点"):
        merge_account_agent_concepts(
            result,
            {
                "node_concepts": [
                    {
                        "level_index": 0,
                        "node_text": "其他应付款",
                        "source_text": "其他应付款",
                        "concept": "other_receivable_payable",
                    }
                ]
            },
            rules,
        )


def test_account_agent_relation_is_kept_as_traceable_fixed_rule_input() -> None:
    rules = load_account_semantic_rules(ROOT)
    result = analyze_account_path("其他应付款_客户特殊款", rules)

    merged = merge_account_agent_concepts(
        result,
        {
            "node_concepts": [
                {
                    "level_index": 1,
                    "node_text": "客户特殊款",
                    "source_text": "客户",
                    "concept": "sales_business",
                }
            ],
            "relations": [
                {
                    "parent_level_index": 0,
                    "child_level_index": 1,
                    "relation": "对象",
                }
            ],
        },
        rules,
    )

    assert merged.relations[0].relation == "对象"
    assert "第1层→第2层=对象" in merged.basis


def test_dictionary_can_choose_different_items_by_cash_direction() -> None:
    dictionary = AccountDictionary((
        AccountSemanticEntry(
            "其他应收款_日常往来",
            "日常经营往来款",
            "",
            "完整路径显示为日常经营往来",
            "medium",
            "custom",
            inflow_item_id="CFO-03",
            outflow_item_id="CFO-07",
        ),
    ))

    inflow = score_dictionary_hits(
        _component(("其他应收款_日常往来",), cash_delta_cent=100), dictionary
    )
    outflow = score_dictionary_hits(
        _component(("其他应收款_日常往来",), cash_delta_cent=-100), dictionary
    )

    assert [(item.item_id, item.score) for item in inflow] == [("CFO-03", 25)]
    assert [(item.item_id, item.score) for item in outflow] == [("CFO-07", 25)]


def test_full_parent_path_is_the_dictionary_identity() -> None:
    dictionary = AccountDictionary((
        AccountSemanticEntry(
            "管理费用_专业服务费",
            "日常经营管理服务",
            "CFO-07",
            "完整父路径显示为管理费用",
            "high",
            "custom",
        ),
        AccountSemanticEntry(
            "在建工程_专业服务费",
            "在建工程相关服务",
            "CFI-06",
            "完整父路径显示为在建工程",
            "high",
            "custom",
        ),
    ))

    management = score_dictionary_hits(
        _component(("管理费用_专业服务费",)), dictionary
    )
    construction = score_dictionary_hits(
        _component(("在建工程_专业服务费",)), dictionary
    )

    assert [(item.item_id, item.score) for item in management] == [("CFO-07", 45)]
    assert [(item.item_id, item.score) for item in construction] == [("CFI-06", 45)]


def test_no_company_special_rule_cannot_replace_general_semantic_judgment() -> None:
    assert refuses_general_semantic_judgment(
        "无企业专属补充语义",
        "没有公司特殊规则，所以不新增企业专属语义",
        "",
    )
    assert not refuses_general_semantic_judgment(
        "日常经营差旅支出，仍可能涉及不同受益对象",
        "完整路径显示为管理费用下的差旅费",
        "",
    )
    assert path_basis_is_traceable(
        "管理费用_差旅费",
        "完整路径“管理费用_差旅费”显示为日常经营管理支出",
    )


def _assert_every_path_node_has_an_explicit_role(result) -> None:
    level_count = len(result.account.split("_"))
    roles_by_level: dict[int, set[str]] = {index: set() for index in range(level_count)}
    for concept in result.concepts:
        roles_by_level[concept.level_index].add(concept.source)
    for slot in result.unresolved_slots:
        roles_by_level[slot.level_index].add("unresolved")

    valid_roles = (
        {"direct"},
        {"parent_inheritance"},
        {"neutral"},
        {"agent"},
        {"unresolved"},
    )
    assert all(roles in valid_roles for roles in roles_by_level.values())


def test_specific_vehicle_tax_full_path_is_strong_tax_evidence() -> None:
    result = analyze_account_path(
        "应交税费_车船税",
        load_account_semantic_rules(ROOT),
    )

    assert result.outflow_candidate_item_ids == ("CFO-06",)
    assert result.quality.value == 45
    assert not result.unresolved_slots
    _assert_every_path_node_has_an_explicit_role(result)


@pytest.mark.parametrize(
    "account_path",
    (
        "应付职工薪酬_社会保险费_基本养老保险",
        "应付职工薪酬_社会保险费_养老",
        "研发支出_费用化支出_直接费用",
        "研发支出_费用化支出_直接投入_直接材料",
        "生产成本_预防成本_培训费",
        "应付票据_银行承兑汇票",
        "长期股权投资_示例科技有限公司",
        "长期股权投资_示例科技有限责任公司",
    ),
)
def test_common_detail_families_keep_every_full_path_node(account_path: str) -> None:
    result = analyze_account_path(account_path, load_account_semantic_rules(ROOT))

    assert not result.unresolved_slots
    _assert_every_path_node_has_an_explicit_role(result)


@pytest.mark.parametrize(
    ("account_path", "expected_item"),
    (
        ("应付职工薪酬_劳务派遣_生产人员", "CFO-05"),
        ("制造费用_外协加工费", "CFO-04"),
        ("管理费用_计量认证费", "CFO-07"),
        ("应付票据_设备款_银行承兑汇票", "CFI-06"),
        ("应付票据_材料款_商业承兑汇票", "CFO-04"),
        ("在建工程_生产线_安装改造费", "CFI-06"),
    ),
)
def test_complete_detail_paths_keep_their_business_substance(
    account_path: str,
    expected_item: str,
) -> None:
    result = analyze_account_path(account_path, load_account_semantic_rules(ROOT))

    assert result.outflow_candidate_item_ids == (expected_item,)
    assert result.quality.value == 45
    assert not result.unresolved_slots
    _assert_every_path_node_has_an_explicit_role(result)


def test_unknown_detail_is_reported_instead_of_being_hidden_by_parent() -> None:
    result = analyze_account_path(
        "其他应付款_火星专用款",
        load_account_semantic_rules(ROOT),
    )

    assert tuple(slot.node_text for slot in result.unresolved_slots) == ("火星专用款",)
    _assert_every_path_node_has_an_explicit_role(result)


@pytest.mark.parametrize(
    "account_path",
    (
        "材料采购_直接材料",
        "在途物资_商品采购",
        "原材料_钢材",
        "委托加工物资_加工费",
        "周转材料_包装物采购",
        "生产成本_外协加工费",
        "制造费用_生产用电费",
        "劳务成本_外部劳务费",
        "合同履约成本_外部履约服务费",
        "主营业务成本_外包服务费",
        "其他业务成本_委托加工",
        "生产成本_鉴定成本_周期试验费_外部试验费",
        "制造费用_间接费用_燃料动力费_水",
        "制造费用_间接费用_燃料动力费_电",
    ),
)
def test_inventory_and_cost_paths_identify_external_operating_inputs(
    account_path: str,
) -> None:
    result = analyze_account_path(account_path, load_account_semantic_rules(ROOT))

    assert result.outflow_candidate_item_ids == ("CFO-04",)
    assert result.quality.value == 45
    assert not result.unresolved_slots
    _assert_every_path_node_has_an_explicit_role(result)


@pytest.mark.parametrize(
    "account_path",
    (
        "周转材料_低值易耗品",
        "周转材料_包装物",
        "材料采购_原材料",
        "委托加工物资_低值易耗品",
    ),
)
def test_direct_inventory_entry_does_not_require_repeated_purchase_wording(
    account_path: str,
) -> None:
    result = analyze_account_path(account_path, load_account_semantic_rules(ROOT))

    assert result.outflow_candidate_item_ids == ("CFO-04",)
    assert result.quality.value == 45
    assert not result.unresolved_slots
    _assert_every_path_node_has_an_explicit_role(result)


@pytest.mark.parametrize(
    "account_path",
    (
        "生产成本_设备折旧",
        "周转材料_摊销",
        "库存商品_完工结转",
        "发出商品_结转主营业务成本",
        "存货跌价准备_计提减值",
    ),
)
def test_internal_inventory_and_cost_transfers_never_create_cash_candidates(
    account_path: str,
) -> None:
    result = analyze_account_path(account_path, load_account_semantic_rules(ROOT))

    assert not result.candidate_item_ids
    assert not result.inflow_candidate_item_ids
    assert not result.outflow_candidate_item_ids
    assert not result.unresolved_slots
    _assert_every_path_node_has_an_explicit_role(result)


@pytest.mark.parametrize(
    "account_path",
    (
        "主营业务成本",
        "库存商品",
        "合同履约成本_差旅费",
        "生产成本_鉴定成本_检验费_试验费",
    ),
)
def test_broad_cost_or_inventory_destination_does_not_prove_a_purchase(
    account_path: str,
) -> None:
    result = analyze_account_path(account_path, load_account_semantic_rules(ROOT))

    assert "CFO-04" not in result.candidate_item_ids
    assert "CFO-04" not in result.outflow_candidate_item_ids
    assert not result.unresolved_slots
    _assert_every_path_node_has_an_explicit_role(result)


def test_staff_cost_still_has_priority_inside_the_cost_chain() -> None:
    result = analyze_account_path(
        "生产成本_人工_福利",
        load_account_semantic_rules(ROOT),
    )

    assert result.outflow_candidate_item_ids == ("CFO-05",)


def test_cost_allocation_marker_does_not_swallow_dividend_distribution_paths() -> None:
    rules = load_account_semantic_rules(ROOT)

    cost_allocation = analyze_account_path("生产成本_制造费用分配", rules)
    dividend_distribution = analyze_account_path("利润分配_应付股利", rules)

    assert cost_allocation.matched_rule_ids == ("PATH-NONCASH",)
    assert "PATH-NONCASH" not in dividend_distribution.matched_rule_ids
