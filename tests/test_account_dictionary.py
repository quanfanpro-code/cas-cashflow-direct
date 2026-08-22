# -*- coding: utf-8 -*-
"""科目语义词典模块测试（Task 4）。"""
from __future__ import annotations

from pathlib import Path

from cashflow_direct.account_dictionary import (
    AccountDictionary,
    AccountSemanticEntry,
    collect_detail_segments,
    load_common_dictionary,
    merge_dictionaries,
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
    entry = dictionary.lookup("应付设备款")
    assert entry is not None and entry.item_id == "CFI-06" and entry.layer == "common"
    assert dictionary.lookup("不存在的科目段") is None


def test_custom_overrides_common():
    common = AccountDictionary((AccountSemanticEntry("应付设备款", "购建", "CFI-06", "依据", "high", "common"),))
    custom = AccountDictionary((AccountSemanticEntry("应付设备款", "特殊业务", "CFI-09", "依据", "high", "custom"),))
    merged = merge_dictionaries(common, custom)
    assert merged.lookup("应付设备款").item_id == "CFI-09"


def test_runtime_interpretation_cannot_override_builtin_common_semantic() -> None:
    common = AccountDictionary((
        AccountSemanticEntry(
            "应付设备款", "购建设备", "CFI-06", "准则通用语义", "high", "common"
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


def test_collect_detail_segments():
    assert collect_detail_segments(["应付账款_应付设备款_往来款", "库存现金", "应交税费_进项税"]) == ("应付设备款", "往来款", "进项税")


def test_score_dictionary_hits_levels_and_confidence():
    dictionary = merge_dictionaries(
        load_common_dictionary(ROOT),
        AccountDictionary((
            AccountSemanticEntry("回购义务", "筹资", "CFF-06", "依据", "high", "custom"),
            AccountSemanticEntry("待抵扣税金", "不确定", "CFO-06", "依据", "medium", "custom"),
            AccountSemanticEntry("低置信段", "不确定", "CFO-07", "依据", "low", "custom"),
            AccountSemanticEntry("进项税", "随交易", "", "依据", "high", "custom"),
        )),
    )
    common_hits = score_dictionary_hits(_component(("应付账款_应付设备款",)), dictionary)
    assert common_hits[0].score == 45 and common_hits[0].item_id == "CFI-06"
    custom_hits = score_dictionary_hits(_component(("其他应付款_回购义务",)), dictionary)
    assert custom_hits[0].score == 45 and custom_hits[0].item_id == "CFF-06"
    medium_hits = score_dictionary_hits(_component(("应交税费_待抵扣税金",)), dictionary)
    assert medium_hits[0].score == 25
    low_hits = score_dictionary_hits(_component(("应交税费_低置信段",)), dictionary)
    assert low_hits[0].score == 10
    assert score_dictionary_hits(_component(("应交税费_进项税",)), dictionary) == ()  # item_id 为空不得分


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
