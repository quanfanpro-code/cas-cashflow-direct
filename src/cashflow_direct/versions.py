from __future__ import annotations

import hashlib
from pathlib import Path


SCHEMA_VERSION = "4.2"
SCORING_VERSION = "2026-08-24-complete-account-path-v15"
ACTION_MATRIX_VERSION = "2026-08-24-selectable-change-threshold-v15"
ACCOUNT_MAPPING_VERSION = "2026-08-21-mapping-first-hard-gate-v2"
COMPANY_NOTES_VERSION = "2026-08-22-scoped-versioned-notes-v2"
SUMMARY_SEMANTICS_VERSION = "2026-08-24-fixed-relations-v3"
MATERIALITY_VERSION = "2026-08-23-single-item-only-v2"
FORCED_CHECKS_VERSION = "2026-08-23-forced-checks-v1"


def _combined_sha256(paths: tuple[Path, ...]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def current_versions(project_root: Path) -> dict[str, str]:
    root = Path(project_root)
    references = root / "references"
    return {
        "schema": SCHEMA_VERSION,
        "scoring": SCORING_VERSION,
        "action_matrix": ACTION_MATRIX_VERSION,
        "account_mapping": ACCOUNT_MAPPING_VERSION,
        "company_notes": COMPANY_NOTES_VERSION,
        "summary_semantics": SUMMARY_SEMANTICS_VERSION,
        "materiality": MATERIALITY_VERSION,
        "forced_checks": FORCED_CHECKS_VERSION,
        "rule_pack": _combined_sha256(
            (
                references / "一般企业正表项目.json",
                references / "摘要语义规则.json",
            )
        ),
        "account_dictionary": _combined_sha256(
            (
                references / "科目语义词典.json",
                references / "字段语义词典.json",
                references / "标准一级科目并集去重表.md",
            )
        ),
    }


def assert_current_versions(
    recorded: object,
    project_root: Path,
) -> None:
    current = current_versions(project_root)
    if not isinstance(recorded, dict):
        raise RuntimeError("旧运行目录缺少版本记录，请新建运行目录后重新处理")
    changed = [
        name for name, value in current.items() if recorded.get(name) != value
    ]
    if changed:
        raise RuntimeError(
            "旧运行目录的评分或规则版本已经变化，请新建运行目录后重新处理；"
            "不一致项目：" + "、".join(changed)
        )
