from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".csv"}
ALLOWED_IMPORTS = {
    "__future__", "argparse", "ast", "collections", "contextlib", "dataclasses", "datetime", "decimal",
    "difflib", "enum", "hashlib", "json", "math", "os", "pathlib", "posixpath", "re", "shutil", "sqlite3",
    "subprocess", "sys", "time", "tkinter", "types", "typing", "unittest", "xml", "zipfile",
    "cashflow_direct", "openpyxl", "pandas", "xlsxwriter",
}
FORBIDDEN_TEXT = (
    "excel-master",
    "cas-cashflow-indirect",
    "cas-cashflow-main-workpaper",
    "complex-table-header",
    "直接法编制现流表\\01",
    "直接法编制现流表\\02",
    "直接法编制现流表\\03",
    "直接法编制现流表\\04",
    "直接法编制现流表\\05",
)


def validate() -> tuple[str, ...]:
    errors: list[str] = []
    required = (
        ROOT / "SKILL.md",
        ROOT / "README.md",
        ROOT / "scripts" / "run_cashflow_direct.py",
        ROOT / "src" / "cashflow_direct" / "pipeline.py",
        ROOT / "references" / "一般企业正表项目.json",
        ROOT / "references" / "直接法分类规则.json",
        ROOT / "references" / "字段语义词典.json",
        ROOT / "references" / "标准一级科目并集去重表.md",
    )
    for path in required:
        if not path.is_file():
            errors.append(f"缺少必要文件：{path.relative_to(ROOT)}")

    runtime_roots = [ROOT / "SKILL.md", ROOT / "README.md", ROOT / "references", ROOT / "scripts", ROOT / "src"]
    runtime_files = []
    for item in runtime_roots:
        runtime_files.extend([item] if item.is_file() else [path for path in item.rglob("*") if path.is_file()])
    for path in runtime_files:
        if path.suffix.lower() in TEXT_SUFFIXES:
            content = path.read_bytes()
            if not content.startswith(bytes.fromhex("EFBBBF")):
                errors.append(f"中文文本缺少 UTF-8 BOM：{path.relative_to(ROOT)}")
                continue
            text = content.decode("utf-8-sig")
            if "\ufffd" in text:
                errors.append(f"存在 Unicode 替换字符：{path.relative_to(ROOT)}")
            for token in FORBIDDEN_TEXT:
                if path.resolve() != Path(__file__).resolve() and token in text:
                    errors.append(f"运行时文件包含外部 Skill 或真实案例标记：{path.relative_to(ROOT)}")

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8-sig") if (ROOT / "SKILL.md").is_file() else ""
    if not skill_text.startswith("---\nname: cas-cashflow-direct\n"):
        errors.append("SKILL.md 前置元数据不完整")
    if ".xls" not in skill_text or "另存为" not in skill_text:
        errors.append("SKILL.md 未包含旧式 xls 拒绝规则")
    baseline_path = ROOT / "references" / "标准一级科目并集去重表.md"
    if baseline_path.is_file():
        baseline_rows = sum(
            1
            for line in baseline_path.read_text(encoding="utf-8-sig").splitlines()
            if line.startswith("|") and line.split("|", 2)[1].strip().isdigit()
        )
        if baseline_rows != 201:
            errors.append(f"标准一级科目基线不是201条：{baseline_rows}")

    for path in (ROOT / "references").glob("*.json"):
        try:
            json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            errors.append(f"JSON 无法解析：{path.name}：{exc}")

    for path in tuple((ROOT / "src").rglob("*.py")) + tuple((ROOT / "scripts").glob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError as exc:
            errors.append(f"Python 语法错误：{path.relative_to(ROOT)}：{exc}")
            continue
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in ALLOWED_IMPORTS:
                        errors.append(f"未批准依赖 {top}：{path.relative_to(ROOT)}")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                module = node.module.split(".")[0]
            if module and module not in ALLOWED_IMPORTS:
                errors.append(f"未批准依赖 {module}：{path.relative_to(ROOT)}")
    return tuple(dict.fromkeys(errors))


def main() -> int:
    errors = validate()
    for error in errors:
        print(f"ERROR={error}")
    print(f"SKILL_VALID={not errors}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
