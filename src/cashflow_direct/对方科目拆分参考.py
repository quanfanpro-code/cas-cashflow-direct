"""调用现有对方科目分析程序，给待定凭证提供原账拆分参考。"""

from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from cashflow_direct.money import yuan_to_cent


def _program_path() -> Path:
    override = os.environ.get("KEMU_COUNTERPART_GENERATOR_REPO")
    return Path(override) if override else Path.home() / "Documents" / "Code" / "kemu-counterpart-generator"


def analyze_counterpart_splits(source_entries: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """返回对方程序的逐行拆分参考；任何结果都不自动变成现金流业务决定。"""
    repo = _program_path()
    if not (repo / "src" / "pipeline" / "group_processor.py").is_file():
        return {"状态": "未找到对方科目分析程序", "程序路径": str(repo)}
    loaded = sys.modules.get("src")
    if loaded is not None and not str(getattr(loaded, "__file__", "") or "").startswith(str(repo)):
        return {"状态": "程序模块名冲突", "程序路径": str(repo)}
    sys.path.insert(0, str(repo))
    try:
        from src.pipeline.group_processor import process_group
    finally:
        sys.path.remove(str(repo))

    original = {}
    rows = []
    for entry in source_entries:
        entry_id = str(entry["entry_id"])
        debit_cent, credit_cent = int(entry["debit_cent"]), int(entry["credit_cent"])
        original[entry_id] = debit_cent - credit_cent
        rows.append({
            "原始行ID": entry_id,
            "一级科目": str(entry["account_name"]),
            "摘要": str(entry.get("summary", "")),
            "借方发生额": debit_cent / 100,
            "贷方发生额": credit_cent / 100,
        })
    if len(original) != len(rows) or not rows:
        raise ValueError("对方科目拆分参考必须使用唯一原始行")
    voucher = str(source_entries[0]["voucher_key"])
    if any(str(entry["voucher_key"]) != voucher for entry in source_entries):
        raise ValueError("对方科目拆分参考不能跨凭证")
    output, warnings = process_group((voucher, pd.DataFrame(rows)))
    reconstructed = defaultdict(int)
    details = []
    for row in output:
        entry_id = str(row["原始行ID"])
        amount = yuan_to_cent(row["借方发生额"]) - yuan_to_cent(row["贷方发生额"])
        reconstructed[entry_id] += amount
        details.append({
            "原始行ID": entry_id,
            "拆分金额分": amount,
            "对方科目": row.get("对方科目"),
            "匹配方式": row.get("匹配类型"),
        })
    if dict(reconstructed) != original:
        return {"状态": "拆分金额与原账不符", "程序路径": str(repo), "警告": warnings}
    return {"状态": "仅供分析", "程序路径": str(repo), "拆分参考": details, "警告": warnings}
