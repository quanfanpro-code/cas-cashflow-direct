"""对方程序仅提供拆分依据，不替现金流技能作业务决定。"""

from dataclasses import asdict
from pathlib import Path

import pytest

from cashflow_direct.对方科目拆分参考 import _program_path, analyze_counterpart_splits
from tests.fixture_factory import _component_entry


def _rows():
    return [asdict(row) for row in (
        _component_entry(1, "V-REF", "银行存款", debit_cent=10000, summary="收回客户欠款"),
        _component_entry(2, "V-REF", "应收账款", credit_cent=10000, summary="收回客户欠款"),
    )]


def test_程序缺失时保留原流程(monkeypatch, tmp_path):
    monkeypatch.setenv("KEMU_COUNTERPART_GENERATOR_REPO", str(tmp_path))
    assert analyze_counterpart_splits(_rows())["状态"] == "未找到对方科目分析程序"


@pytest.mark.skipif(not (_program_path() / "src" / "pipeline" / "group_processor.py").is_file(),
                    reason="本机未安装对方科目分析程序")
def test_真实程序返回可追溯且金额闭合的拆分参考():
    result = analyze_counterpart_splits(_rows())
    assert result["状态"] == "仅供分析"
    assert {(row["原始行ID"], row["拆分金额分"], row["对方科目"])
            for row in result["拆分参考"]} == {
        ("E1", 10000, "应收账款"), ("E2", -10000, "银行存款"),
    }
