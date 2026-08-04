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


if __name__ == "__main__":
    unittest.main()
