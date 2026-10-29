from __future__ import annotations

import tempfile
from pathlib import Path

from openpyxl import load_workbook

from cashflow_direct.workbook_output import SHEET_NAMES, build_output_workbook
from tests.fixture_factory import workbook_model


def test_summary_and_machine_records_are_not_separate_human_sheets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "精简工作簿.xlsx"
        build_output_workbook(workbook_model(0, 0), path)
        workbook = load_workbook(path)
        try:
            assert workbook.sheetnames == list(SHEET_NAMES)
            assert "分类汇总视图" not in workbook.sheetnames
            assert "输入识别与字段映射" not in workbook.sheetnames
        finally:
            workbook.close()
