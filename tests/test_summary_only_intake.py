from pathlib import Path

import pytest
from openpyxl import Workbook

from cashflow_direct.pipeline import run_preflight


def test_summary_only_cashflow_detail_requests_journal_before_run(tmp_path: Path) -> None:
    source = tmp_path / "现流明细.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["日期", "凭证号", "现金流量项目", "摘要", "方向", "金额"])
    sheet.append(["2026-01-04", "记-0006", "销售商品、提供劳务收到的现金", "收货款", "流入", 100])
    workbook.save(source)
    workbook.close()

    with pytest.raises(ValueError, match="补充可按凭证号对应的本期完整序时账"):
        run_preflight([source], (100000, 50000, 5000), tmp_path / "输出")
    assert not (tmp_path / "输出").exists()
