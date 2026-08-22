from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


_RECALCULATION_SCRIPT = r"""
$OutputEncoding = [Console]::OutputEncoding = [Text.Encoding]::UTF8
$path = [Environment]::GetEnvironmentVariable('CAS_CASHFLOW_EXCEL_RECALC_PATH')
$excel = $null
$book = $null
try {
  $excel = New-Object -ComObject Excel.Application
  $excel.Visible = $false
  $excel.DisplayAlerts = $false
  $excel.ScreenUpdating = $false
  $book = $excel.Workbooks.Open($path, 0, $false)
  $excel.CalculateFullRebuild()
  if ($excel.CalculationState -ne 0) {
    throw "Excel完整重算未完成，状态=$($excel.CalculationState)"
  }
  $book.Save()
  Write-Output 'RECALC_OK'
} finally {
  if ($book -ne $null) { $book.Close($false) }
  if ($excel -ne $null) { $excel.Quit() }
  if ($book -ne $null) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($book) }
  if ($excel -ne $null) { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel) }
  [GC]::Collect()
  [GC]::WaitForPendingFinalizers()
}
"""


def recalculate_workbook_with_excel(path: Path) -> None:
    workbook_path = Path(path).resolve()
    if not workbook_path.is_file():
        raise RuntimeError(f"Excel完整重算失败：工作簿不存在：{workbook_path}")
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        raise RuntimeError("Excel完整重算失败：未找到PowerShell")
    environment = dict(os.environ)
    environment["CAS_CASHFLOW_EXCEL_RECALC_PATH"] = str(workbook_path)
    try:
        completed = subprocess.run(
            (
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                _RECALCULATION_SCRIPT,
            ),
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Excel完整重算失败：等待Excel超过120秒") from error
    output = "\n".join((completed.stdout or "", completed.stderr or "")).strip()
    if completed.returncode != 0 or "RECALC_OK" not in output:
        raise RuntimeError(f"Excel完整重算失败：{output or '没有取得Excel完成标记'}")
