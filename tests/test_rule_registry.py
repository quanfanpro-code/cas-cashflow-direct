from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cashflow_direct.decision_policy import (
    MaterialityLevel,
    OriginalItemState,
    route_decision,
)
from cashflow_direct.rule_registry import RuleRegistryError, load_rule_registry
from cashflow_direct.versions import current_versions


ROOT = Path(__file__).resolve().parents[1]


def test_rule_registry_loads_every_current_rule_category_from_one_entry() -> None:
    registry = load_rule_registry(ROOT)

    assert registry.categories == {
        "正表项目",
        "字段识别",
        "完整科目路径",
        "摘要语义",
        "证据评分与行动",
        "特殊业务与勾稽",
        "工作簿输出",
        "一级科目基线",
    }
    assert len(registry.statement_item_ids) == 35
    assert registry.output_sheet_names[4] == "低金额系统兜底明细"
    assert len(registry.output_sheet_names) == 13
    assert registry.difference_source_order == ("account_path", "summary")


def test_deprecated_low_amount_actions_are_not_active_or_routable() -> None:
    registry = load_rule_registry(ROOT)

    assert "low_amount_human_batch" not in registry.active_actions
    assert "human_batch" not in registry.active_actions
    assert not registry.deprecated_actions.intersection(registry.active_actions)
    route = route_decision(
        25,
        OriginalItemState.BLANK,
        MaterialityLevel.M0,
        business_conflict=True,
    )
    assert route.action.value == "human_decision"


def test_every_action_matrix_cell_has_rule_id_and_known_action() -> None:
    registry = load_rule_registry(ROOT)

    for original_group in ("agrees", "valid_original", "blank"):
        for score in registry.allowed_scores:
            for level in ("M0", "M1", "M2", "M3"):
                cell = registry.normal_action_cell(original_group, score, level)
                assert cell["rule_id"]
                assert cell["action"] in registry.active_actions


def test_every_registered_rule_has_complete_trace_metadata() -> None:
    registry = load_rule_registry(ROOT)

    assert registry.rule_records
    required = {
        "rule_id",
        "name",
        "category",
        "applicable_condition",
        "not_applicable_condition",
        "result",
        "priority",
        "reason",
        "status",
        "version",
        "source_file",
        "test_reference",
    }
    assert all(required.issubset(record) for record in registry.rule_records)
    assert all(record["test_reference"] for record in registry.rule_records)


def test_rule_registry_rejects_duplicate_rule_ids(tmp_path: Path) -> None:
    copied = tmp_path / "skill"
    shutil.copytree(ROOT / "references", copied / "references")
    policy_path = copied / "references" / "证据评分与行动规则.json"
    payload = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    payload["rule_metadata"].append(dict(payload["rule_metadata"][0]))
    policy_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuleRegistryError, match="规则编号重复"):
        load_rule_registry(copied)


def test_rule_registry_rejects_unknown_item_and_direction_conflict(tmp_path: Path) -> None:
    copied = tmp_path / "skill"
    shutil.copytree(ROOT / "references", copied / "references")
    statement_path = copied / "references" / "一般企业正表项目.json"
    payload = json.loads(statement_path.read_text(encoding="utf-8-sig"))
    payload["statement_items"][0]["normal_direction"] = "sideways"
    statement_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(RuleRegistryError, match="现金方向非法"):
        load_rule_registry(copied)


def test_rule_registry_rejects_missing_file_and_priority_conflict(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    shutil.copytree(ROOT / "references", missing / "references")
    (missing / "references" / "工作簿输出规则.json").unlink()
    with pytest.raises(RuleRegistryError, match="登记文件不存在"):
        load_rule_registry(missing)

    conflict = tmp_path / "conflict"
    shutil.copytree(ROOT / "references", conflict / "references")
    policy_path = conflict / "references" / "证据评分与行动规则.json"
    payload = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    payload["rule_metadata"].append(
        {
            "rule_id": "POLICY-CONFLICT-TEST",
            "name": "优先级冲突测试",
            "category": "行动路由",
            "priority": 10,
            "reason": "测试",
            "status": "active",
            "version": "1",
        }
    )
    policy_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(RuleRegistryError, match="重复优先级"):
        load_rule_registry(conflict)


def test_rule_center_fingerprint_covers_all_current_rule_files() -> None:
    registry = load_rule_registry(ROOT)
    versions = current_versions(ROOT)

    assert versions["rule_center"] == registry.fingerprint
    assert len(versions["rule_center"]) == 64
