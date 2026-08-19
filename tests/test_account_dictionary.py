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
    score_dictionary_hits,
)
from cashflow_direct.models import CashflowComponent

ROOT = Path(__file__).resolve().parents[1]


def _component(accounts=()) -> CashflowComponent:
    return CashflowComponent(
        component_id="CMP-1", voucher_key="K", summary="", cash_delta_cent=-100,
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
    assert common_hits[0].score == 30 and common_hits[0].item_id == "CFI-06"
    custom_hits = score_dictionary_hits(_component(("其他应付款_回购义务",)), dictionary)
    assert custom_hits[0].score == 40 and custom_hits[0].item_id == "CFF-06"
    medium_hits = score_dictionary_hits(_component(("应交税费_待抵扣税金",)), dictionary)
    assert medium_hits[0].score == 30  # 40 - 10
    assert score_dictionary_hits(_component(("应交税费_低置信段",)), dictionary) == ()
    assert score_dictionary_hits(_component(("应交税费_进项税",)), dictionary) == ()  # item_id 为空不得分