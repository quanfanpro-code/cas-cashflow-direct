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
            self.assertIn("十四张", current_text, path.name)
            self.assertIn("分类汇总视图", current_text, path.name)
            self.assertIn("低金额系统兜底明细", current_text, path.name)
            self.assertNotIn("低金额人工批量", current_text, path.name)
            self.assertNotIn("固定包含十二张", current_text, path.name)
            self.assertNotIn("工作簿恰有十二张", current_text, path.name)
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

    def test_runtime_rule_files_are_loaded_through_the_registry(self) -> None:
        architecture = ROOT / "docs" / "architecture" / "2026-08-26-统一规则中心.md"
        self.assertTrue(architecture.is_file())
        self.assertIn("唯一入口", architecture.read_text(encoding="utf-8-sig"))

        direct_rule_names = (
            "一般企业正表项目.json",
            "字段语义词典.json",
            "科目语义词典.json",
            "摘要语义规则.json",
            "标准一级科目并集去重表.md",
        )
        allowed = {"rule_registry.py", "versions.py"}
        for path in (ROOT / "src" / "cashflow_direct").glob("*.py"):
            if path.name in allowed:
                continue
            text = path.read_text(encoding="utf-8-sig")
            for rule_name in direct_rule_names:
                self.assertNotIn(rule_name, text, path.name)


if __name__ == "__main__":
    unittest.main()
