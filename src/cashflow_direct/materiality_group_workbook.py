from __future__ import annotations

from collections.abc import Mapping, Sequence
GROUP_CONFIRMATION_SHEET = "可靠同类组批量处理"
CONFIRM_ALL = "确认全部可靠组"
CONFIRM_GROUP = "确认本组全部处理结果"
DEFER_GROUP = "暂不确认"

ACTION_LABELS = {
    "automatic_fill": "自动填写",
    "automatic_keep": "自动保留原项目",
    "ai_review": "AI复核",
    "double_ai_review": "两个AI独立复核",
    "human_decision": "逐笔人工判断",
    "isolate_invalid_input": "非法输入隔离",
}

DIRECTION_LABELS = {"inflow": "流入", "outflow": "流出"}
MATERIALITY_LABELS = {
    "M0": "低于明显微小错报临界值",
    "M1": "达到明显微小错报临界值但低于实际执行重要性",
    "M2": "达到实际执行重要性但低于整体重要性",
    "M3": "达到整体重要性",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _amount_yuan(value: object) -> float:
    try:
        return int(value or 0) / 100
    except (TypeError, ValueError):
        return 0.0


def _date_only(value: object) -> str:
    text = _text(value)
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return text


def write_materiality_group_sheet(
    workbook,
    sheet,
    requests: Sequence[Mapping[str, object]],
    components: Sequence[Mapping[str, object]],
    decisions: Sequence[Mapping[str, object]],
    assessments: Sequence[Mapping[str, object]],
) -> None:
    """在最终底稿内写入可靠同类组一次确认页。"""
    request_rows = tuple(requests)
    component_by_id = {
        _text(item.get("component_id")): item for item in components
    }
    decision_by_id = {
        _text(item.get("component_id")): item for item in decisions
    }
    assessment_by_id = {
        _text(item.get("record_id")): item for item in assessments
    }
    sheet.set_default_row(18)
    sheet.hide_gridlines(2)

    title = workbook.add_format(
        {"font_name": "Times New Roman", "bold": True, "font_size": 16, "font_color": "#FFFFFF", "bg_color": "#1F4E78", "align": "left", "valign": "vcenter"}
    )
    note = workbook.add_format(
        {"font_name": "Times New Roman", "font_color": "#44546A", "bg_color": "#D9EAF7", "text_wrap": True, "valign": "vcenter"}
    )
    label = workbook.add_format({"font_name": "Times New Roman", "bold": True, "bg_color": "#D9EAF7", "border": 1})
    input_cell = workbook.add_format({"font_name": "Times New Roman", "bg_color": "#FFF2CC", "border": 1, "locked": False})
    header = workbook.add_format(
        {"font_name": "Times New Roman", "bold": True, "font_color": "#FFFFFF", "bg_color": "#4472C4", "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True}
    )
    body = workbook.add_format({"font_name": "Times New Roman", "border": 1, "valign": "top", "text_wrap": True})
    body_center = workbook.add_format({"font_name": "Times New Roman", "border": 1, "align": "center", "valign": "top", "text_wrap": True})
    money = workbook.add_format({"font_name": "Times New Roman", "border": 1, "num_format": "#,##0.00;[Red]-#,##0.00", "valign": "top"})
    group_choice = workbook.add_format({"font_name": "Times New Roman", "bg_color": "#FFF2CC", "border": 1, "locked": False, "align": "center"})
    formula_cell = workbook.add_format({"font_name": "Times New Roman", "bg_color": "#E2F0D9", "border": 1, "align": "center"})
    link = workbook.add_format({"font_name": "Times New Roman", "border": 1, "font_color": "#0563C1", "underline": True, "align": "center"})
    detail_header = workbook.add_format(
        {"font_name": "Times New Roman", "bold": True, "font_color": "#FFFFFF", "bg_color": "#5B9BD5", "border": 1, "align": "center", "valign": "vcenter", "text_wrap": True}
    )

    sheet.merge_range("A1:N1", "可靠同类组批量处理", title)
    sheet.merge_range(
        "A2:N2",
        "程序处理已经完成。只在黄色单元格进行最后人工确认；一组确认一次，公式会把结果同步到该组全部明细。",
        note,
    )
    sheet.write("A3", "处理人（选填）", label)
    sheet.write_blank("B3", None, input_cell)
    sheet.write("A4", "统一确认依据（选填）", label)
    sheet.write_blank("B4", None, input_cell)
    sheet.write("A5", "批量选择", label)
    sheet.write_blank("B5", None, input_cell)
    sheet.write("A6", "确认日期（选填）", label)
    sheet.write_blank("B6", None, input_cell)
    sheet.merge_range("C3:N5", "批量确认：B5选择“确认全部可靠组”。如某一组暂不确认，在该组C列选择“暂不确认”，即可作为本批例外。确认完成后，本工作簿就是完整结果，无需再次运行程序。", note)
    sheet.data_validation("B5", {"validate": "list", "source": [CONFIRM_ALL, "不批量处理"]})

    summary_headers = (
        "序号",
        "生效结果(公式)",
        "本组单独选择",
        "现金方向",
        "最终处理项目",
        "标准一级科目",
        "明确用途",
        "同类累计金额",
        "组内笔数",
        "最大单笔金额",
        "单笔达到整体重要性笔数",
        "当前待确认笔数",
        "查看组内明细",
        "组编号(技术)",
    )
    summary_header_row = 6
    for column, value in enumerate(summary_headers):
        sheet.write(summary_header_row, column, value, header)

    group_detail_start: dict[str, int] = {}
    detail_header_row = summary_header_row + len(request_rows) + 3
    detail_headers = (
        "组内序号",
        "本组生效结果(公式)",
        "日期",
        "凭证号",
        "摘要",
        "完整对方科目路径",
        "金额",
        "原现流项目",
        "最终处理项目",
        "证据得分",
        "单笔重要性",
        "已完成处理动作",
        "组内处理说明",
        "组编号(技术)",
    )
    for column, value in enumerate(detail_headers):
        sheet.write(detail_header_row, column, value, detail_header)

    detail_row = detail_header_row + 1
    for request in request_rows:
        group_id = _text(request.get("group_id"))
        group_detail_start[group_id] = detail_row + 1
        for member_index, component_id_raw in enumerate(request.get("component_ids", ()), 1):
            component_id = _text(component_id_raw)
            component = component_by_id.get(component_id, {})
            decision = decision_by_id.get(component_id, {})
            assessment = assessment_by_id.get(component_id, {})
            summary_first = summary_header_row + 2
            summary_last = summary_header_row + 1 + len(request_rows)
            excel_row = detail_row + 1
            sheet.write_number(detail_row, 0, member_index, body_center)
            sheet.write_formula(
                detail_row,
                1,
                f'=IFERROR(INDEX($B${summary_first}:$B${summary_last},MATCH(N{excel_row},$N${summary_first}:$N${summary_last},0)),"")',
                formula_cell,
                "",
            )
            sheet.write(detail_row, 2, _date_only(component.get("voucher_date")), body)
            sheet.write(detail_row, 3, _text(component.get("voucher_no")), body)
            sheet.write(detail_row, 4, _text(component.get("summary")), body)
            counterparts = component.get("counterpart_accounts", ())
            counterpart_text = "；".join(_text(item) for item in counterparts) if isinstance(counterparts, (list, tuple)) else _text(counterparts)
            sheet.write(detail_row, 5, counterpart_text, body)
            sheet.write_number(detail_row, 6, _amount_yuan(component.get("cash_delta_cent")), money)
            sheet.write(detail_row, 7, _text(component.get("original_item_text")), body)
            sheet.write(detail_row, 8, _text(decision.get("system_item_name")), body)
            score = decision.get("evidence_score")
            if isinstance(score, (int, float)):
                sheet.write_number(detail_row, 9, score, body_center)
            else:
                sheet.write(detail_row, 9, _text(score), body_center)
            level = _text(assessment.get("single_level"))
            sheet.write(
                detail_row,
                10,
                MATERIALITY_LABELS.get(level, level),
                body_center,
            )
            action = _text(decision.get("decision_action"))
            sheet.write(detail_row, 11, ACTION_LABELS.get(action, action), body)
            sheet.write(detail_row, 12, "程序处理已完成；本页只作最后人工确认", body)
            sheet.write(detail_row, 13, group_id, body)
            detail_row += 1

    for group_index, request in enumerate(request_rows, 1):
        row = summary_header_row + group_index
        excel_row = row + 1
        group_id = _text(request.get("group_id"))
        key = tuple(request.get("group_key", ()))
        member_ids = tuple(_text(item) for item in request.get("component_ids", ()))
        member_assessments = [assessment_by_id.get(item, {}) for item in member_ids]
        first_decision = decision_by_id.get(member_ids[0], {}) if member_ids else {}
        member_amounts = [abs(int(item.get("single_amount_cent", 0) or 0)) for item in member_assessments]
        single_m3_count = sum(_text(item.get("single_level")) == "M3" for item in member_assessments)
        pending_count = len(member_ids)
        sheet.write_number(row, 0, group_index, body_center)
        sheet.write_formula(row, 1, f'=IF(C{excel_row}<>"",C{excel_row},IF($B$5="{CONFIRM_ALL}","{CONFIRM_GROUP}",""))', formula_cell, "")
        sheet.write_blank(row, 2, None, group_choice)
        direction = _text(key[0]) if len(key) > 0 else ""
        sheet.write(row, 3, DIRECTION_LABELS.get(direction, direction), body_center)
        sheet.write(row, 4, _text(first_decision.get("system_item_name")) or (_text(key[1]) if len(key) > 1 else ""), body)
        sheet.write(row, 5, _text(key[2]) if len(key) > 2 else "", body)
        purpose = _text(key[3]) if len(key) > 3 else ""
        sheet.write(row, 6, purpose, body)
        sheet.write_number(row, 7, _amount_yuan(request.get("same_class_total_cent")), money)
        sheet.write_number(row, 8, int(request.get("component_count", len(member_ids)) or 0), body_center)
        sheet.write_number(row, 9, max(member_amounts, default=0) / 100, money)
        sheet.write_number(row, 10, single_m3_count, body_center)
        sheet.write_number(row, 11, pending_count, body_center)
        detail_target = group_detail_start.get(group_id, detail_header_row + 2)
        sheet.write_url(row, 12, f"internal:'{GROUP_CONFIRMATION_SHEET}'!A{detail_target}", link, string="查看明细")
        sheet.write(row, 13, group_id, body)
        sheet.data_validation(row, 2, row, 2, {"validate": "list", "source": [CONFIRM_GROUP, DEFER_GROUP]})

    sheet.set_row(0, 28)
    sheet.set_row(1, 34)
    sheet.set_row(2, None, None, {"hidden": True})
    sheet.set_row(3, None, None, {"hidden": True})
    sheet.set_row(5, None, None, {"hidden": True})
    sheet.set_column("A:A", 10)
    sheet.set_column("B:C", 18)
    sheet.set_column("D:D", 12)
    sheet.set_column("E:G", 22)
    sheet.set_column("H:H", 16)
    sheet.set_column("I:L", 14)
    sheet.set_column("M:M", 14)
    sheet.set_column("N:N", 18, None, {"hidden": True})
    sheet.set_column("A:A", 10, None, None)
    sheet.autofilter(summary_header_row, 0, summary_header_row + len(request_rows), len(summary_headers) - 1)
    sheet.freeze_panes(summary_header_row + 1, 3)
    sheet.protect("", {"select_locked_cells": False, "select_unlocked_cells": True, "autofilter": True})
