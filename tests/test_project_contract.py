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

    def test_user_documents_describe_the_ten_sheet_difference_output(self) -> None:
        documents = (
            ROOT / "README.md",
            ROOT / "SKILL.md",
            ROOT / "references" / "使用说明.md",
        )
        for path in documents:
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("十张", text, path.name)
            self.assertIn("原表与自动判定差异", text, path.name)
            self.assertNotIn("九张", text, path.name)


if __name__ == "__main__":
    unittest.main()
