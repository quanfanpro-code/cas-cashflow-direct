from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path

from openpyxl import load_workbook

from cashflow_direct.models import ReviewBatch
from cashflow_direct.workbook_output import REVIEW_HEADERS, build_output_workbook
from tests.fixture_factory import workbook_model


def trace_rows() -> tuple[dict[str, object], ...]:
    common = {
        "业务组成编号(技术)": "RC-1",
        "日期": "2025-03-22",
        "凭证字": "记",
        "凭证号": "146",
        "本行完整对方科目路径": "主营业务收入",
        "标准一级科目": "主营业务收入",
        "现金账户路径": "银行存款_民生银行成都分行2385",
        "现金方向依据": "借贷差额",
        "原项目标准化结果": "原项目为空",
        "系统候选项目": "销售商品、提供劳务收到的现金",
        "判断理由": "等待人工决定",
    }
    return (
        {
            **common,
            "本行摘要": "收3.21日营业款",
            "借方": 34_352.90,
            "贷方": 0.0,
            "流量金额（原币）": 34_352.90,
            "本行分配现金变化": 34_352.90,
        },
        {
            **common,
            "本行摘要": "收3.22日营业款",
            "借方": 97_631.43,
            "贷方": 0.0,
            "流量金额（原币）": 97_631.43,
            "本行分配现金变化": 97_631.43,
        },
    )


def test_all_manual_sheets_show_one_numeric_amount_per_detail_row() -> None:
    base = workbook_model(1, 0)
    important = replace(
        base.review_batches[0],
        component_ids=("RC-1",),
        cash_delta_cent=13_198_433,
    )
    low = ReviewBatch(
        batch_id="LOW-1",
        component_ids=("RC-1",),
        proposed_item_code="CFO-01",
        alternative_item_codes=("CFO-03",),
        worst_case_impact_cent=13_198_433,
        reason="低金额批量",
        cash_delta_cent=13_198_433,
    )
    model = replace(
        base,
        review_batches=(important,),
        low_amount_review_batches=(low,),
        trace_rows=trace_rows(),
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "人工事项逐行金额.xlsx"
        build_output_workbook(model, path)
        workbook = load_workbook(path, data_only=False)
        try:
            assert "低金额批量处理" in workbook.sheetnames
            for sheet_name in ("重要待复核事项", "低金额批量处理"):
                sheet = workbook[sheet_name]
                headers = [cell.value for cell in sheet[1]]
                row_type_col = headers.index("行类型") + 1
                amount_col = headers.index("本行分配现金变化") + 1
                detail_rows = [
                    row
                    for row in range(2, sheet.max_row + 1)
                    if sheet.cell(row, row_type_col).value == "现金分配明细"
                ]
                assert len(detail_rows) == 2
                assert [sheet.cell(row, amount_col).value for row in detail_rows] == [
                    34_352.90,
                    97_631.43,
                ]
                assert all(
                    isinstance(sheet.cell(row, amount_col).value, (int, float))
                    for row in detail_rows
                )
                for header in (
                    "借方",
                    "贷方",
                    "流量金额（原币）",
                    "本行分配现金变化",
                    "单笔金额",
                ):
                    column = headers.index(header) + 1
                    assert all(
                        sheet.cell(row, column).value in (None, "")
                        or isinstance(sheet.cell(row, column).value, (int, float))
                        for row in detail_rows
                    )
        finally:
            workbook.close()


def test_low_amount_batch_has_one_unlocked_master_choice_and_details_follow_it() -> None:
    base = workbook_model(0, 0)
    low = ReviewBatch(
        batch_id="LOW-1",
        component_ids=("RC-1",),
        proposed_item_code="CFO-01",
        alternative_item_codes=("CFO-03",),
        worst_case_impact_cent=13_198_433,
        reason="低金额批量",
        cash_delta_cent=13_198_433,
    )
    model = replace(
        base,
        low_amount_review_batches=(low,),
        trace_rows=trace_rows(),
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "低金额整批生效.xlsx"
        build_output_workbook(model, path)
        workbook = load_workbook(path, data_only=False)
        try:
            sheet = workbook["低金额批量处理"]
            headers = [cell.value for cell in sheet[1]]
            row_type_col = headers.index("行类型") + 1
            choice_col = headers.index("人工确认项目") + 1
            master_row = next(
                row
                for row in range(2, sheet.max_row + 1)
                if sheet.cell(row, row_type_col).value == "批次判断"
            )
            detail_rows = [
                row
                for row in range(2, sheet.max_row + 1)
                if sheet.cell(row, row_type_col).value == "现金分配明细"
            ]

            assert sheet.cell(master_row, choice_col).protection.locked is False
            assert all(sheet.cell(row, choice_col).data_type == "f" for row in detail_rows)
            assert all(
                f"{sheet.cell(master_row, choice_col).coordinate}" in sheet.cell(row, choice_col).value
                for row in detail_rows
            )
        finally:
            workbook.close()
