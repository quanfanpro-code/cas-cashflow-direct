from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProjectContractTests(unittest.TestCase):
    def test_skill_and_cli_entry_exist(self) -> None:
        self.assertTrue((ROOT / "SKILL.md").is_file())
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_cashflow_direct.py"), "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env={**os.environ, "PYTHONIOENCODING": "UTF-8", "PYTHONUTF8": "1"},
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn("直接法现金流量表", completed.stdout)
        self.assertIn("confirm-mapping", completed.stdout)
        self.assertIn("confirm-account-mapping", completed.stdout)
        self.assertIn("import-ai", completed.stdout)

    def test_runtime_files_do_not_reference_other_skills(self) -> None:
        forbidden = (
            "excel-master",
            "cas-cashflow-indirect",
            "cas-cashflow-main-workpaper",
            "complex-table-header",
        )
        runtime = [ROOT / "SKILL.md", ROOT / "scripts" / "run_cashflow_direct.py"]
        text = "\n".join(path.read_text(encoding="utf-8-sig") for path in runtime)
        for token in forbidden:
            self.assertNotIn(token, text)

    def test_user_documents_describe_the_thirteen_sheet_output_without_reliable_groups(self) -> None:
        documents = (
            ROOT / "README.md",
            ROOT / "SKILL.md",
        )
        for path in documents:
            text = path.read_text(encoding="utf-8-sig")
            current_text = text.split("## 🗓️ 更新记录", 1)[0]
            self.assertIn("十三张", current_text, path.name)
            self.assertIn("低金额批量处理", current_text, path.name)
            self.assertIn("原表与系统决定差异", text, path.name)
            self.assertNotIn("可靠同类组批量处理", current_text, path.name)
            self.assertNotIn("原表与自动判定差异", text, path.name)

    def test_user_documents_state_the_change_burden_and_batch_shortcut(self) -> None:
        for path in (ROOT / "README.md", ROOT / "SKILL.md"):
            text = path.read_text(encoding="utf-8-sig")
            current_text = text.split("## 🗓️ 更新记录", 1)[0]
            self.assertIn("异常纠偏系统", text, path.name)
            self.assertIn("修改原项目", text, path.name)
            self.assertIn("采用系统首选项目", text, path.name)
            self.assertIn("原项目有效且低于整体重要性", current_text, path.name)


if __name__ == "__main__":
    unittest.main()
