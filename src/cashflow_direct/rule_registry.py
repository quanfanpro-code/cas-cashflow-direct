from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class RuleRegistryError(ValueError):
    """规则中心结构、引用或规则编号不合法。"""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            payload = json.load(source)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuleRegistryError(f"规则文件无法读取：{path.name}；{exc}") from exc
    if not isinstance(payload, dict):
        raise RuleRegistryError(f"规则文件顶层必须是对象：{path.name}")
    return payload


def _walk_rule_metadata(value: object):
    if isinstance(value, dict):
        rule_id = value.get("rule_id")
        if rule_id:
            yield value
        for child in value.values():
            yield from _walk_rule_metadata(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_rule_metadata(child)


def _walk_candidate_item_ids(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith("candidate_item_ids"):
                if not isinstance(child, list):
                    raise RuleRegistryError(f"{key} 必须是项目编号数组")
                yield from child
            else:
                yield from _walk_candidate_item_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_candidate_item_ids(child)


def _walk_single_item_ids(value: object):
    if isinstance(value, dict):
        for key, child in value.items():
            if key != "rule_id" and (key == "item_id" or key.endswith("_item_id")):
                if child:
                    yield child
            elif key == "direction_default_items" and isinstance(child, dict):
                yield from child.values()
            else:
                yield from _walk_single_item_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_single_item_ids(child)


@dataclass(frozen=True, slots=True)
class RuleRegistry:
    project_root: Path
    manifest: dict[str, Any]
    payloads: dict[str, object]
    categories: frozenset[str]
    statement_item_ids: frozenset[str]
    output_sheet_names: tuple[str, ...]
    active_actions: frozenset[str]
    deprecated_actions: frozenset[str]
    allowed_scores: tuple[int, ...]
    automatic_change_score_options: tuple[int, ...]
    default_automatic_change_score: int
    difference_source_order: tuple[str, ...]
    fingerprint: str

    @property
    def statement_policy(self) -> dict[str, Any]:
        return self.payloads["statement_items"]  # type: ignore[return-value]

    @property
    def field_semantics(self) -> dict[str, Any]:
        return self.payloads["field_semantics"]  # type: ignore[return-value]

    @property
    def account_semantics(self) -> dict[str, Any]:
        return self.payloads["account_semantics"]  # type: ignore[return-value]

    @property
    def summary_semantics(self) -> dict[str, Any]:
        return self.payloads["summary_semantics"]  # type: ignore[return-value]

    @property
    def level1_baseline_text(self) -> str:
        return self.payloads["level1_baseline"]  # type: ignore[return-value]

    @property
    def evidence_policy(self) -> dict[str, Any]:
        return self.payloads["evidence_and_actions"]  # type: ignore[return-value]

    @property
    def special_policy(self) -> dict[str, Any]:
        return self.payloads["special_and_reconciliation"]  # type: ignore[return-value]

    @property
    def output_policy(self) -> dict[str, Any]:
        return self.payloads["workbook_output"]  # type: ignore[return-value]

    def action_group(self, name: str) -> frozenset[str]:
        try:
            values = self.evidence_policy["action_groups"][name]
        except (KeyError, TypeError) as exc:
            raise RuleRegistryError(f"行动分组不存在：{name}") from exc
        unknown = set(values).difference(self.active_actions)
        if unknown:
            raise RuleRegistryError(
                f"行动分组 {name} 引用未启用动作：{'、'.join(sorted(unknown))}"
            )
        return frozenset(str(value) for value in values)

    @property
    def rule_records(self) -> tuple[dict[str, object], ...]:
        """把不同类别的规则整理成同一套可追溯登记字段。"""
        catalog_by_key = {
            str(entry["key"]): entry
            for entry in self.manifest["rule_files"]
            if isinstance(entry, dict)
        }
        records: list[dict[str, object]] = []
        for key, payload in self.payloads.items():
            if not isinstance(payload, dict):
                continue
            catalog = catalog_by_key[key]
            schema_version = str(payload.get("schema_version", "未记录"))
            for sequence, rule in enumerate(_walk_rule_metadata(payload), 1):
                conditions = {
                    name: value
                    for name, value in rule.items()
                    if name.startswith(("require", "level", "min_"))
                }
                exclusions = {
                    name: value
                    for name, value in rule.items()
                    if name in {"forbid", "stop"}
                }
                result = {
                    name: value
                    for name, value in rule.items()
                    if name.endswith("candidate_item_ids")
                    or name in {"action", "status", "semantic"}
                }
                records.append(
                    {
                        "rule_id": str(rule["rule_id"]),
                        "name": str(
                            rule.get("name")
                            or rule.get("semantic")
                            or rule["rule_id"]
                        ),
                        "category": str(rule.get("category") or catalog["category"]),
                        "applicable_condition": conditions or "详见所属规则表",
                        "not_applicable_condition": exclusions or "无额外排除条件",
                        "result": result or "详见所属规则表",
                        "priority": int(rule.get("priority", sequence)),
                        "reason": str(
                            rule.get("reason")
                            or rule.get("semantic")
                            or payload.get("说明")
                            or "按所属规则表执行"
                        ),
                        "status": str(rule.get("status", "active")),
                        "version": str(rule.get("version", schema_version)),
                        "source_file": str(catalog["path"]),
                        "test_reference": str(catalog["test_reference"]),
                    }
                )
        return tuple(records)

    def normal_action_cell(
        self,
        original_group: str,
        score: int,
        level: str,
    ) -> dict[str, str]:
        try:
            action, review_policy = self.evidence_policy["normal_action_matrix"][
                original_group
            ][str(score)][level]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleRegistryError(
                f"正常行动表缺少单元格：{original_group}/{score}/{level}"
            ) from exc
        if action not in self.active_actions:
            raise RuleRegistryError(f"正常行动表引用未启用动作：{action}")
        return {
            "rule_id": f"POLICY-NORMAL-ACTION:{original_group}:{score}:{level}",
            "action": action,
            "review_policy": review_policy,
        }

    def forced_route_cell(
        self,
        forced_kind: str,
        original_group: str,
        level: str,
    ) -> dict[str, str]:
        try:
            route = self.evidence_policy["forced_routes"][forced_kind]
            cell = route["all"] if "all" in route else route[original_group][level]
            action, review_policy = cell
        except (KeyError, TypeError, ValueError) as exc:
            raise RuleRegistryError(
                f"强制行动表缺少单元格：{forced_kind}/{original_group}/{level}"
            ) from exc
        if action not in self.active_actions:
            raise RuleRegistryError(f"强制行动表引用未启用动作：{action}")
        return {
            "rule_id": f"POLICY-FORCED-CHECK:{forced_kind}:{original_group}:{level}",
            "action": action,
            "review_policy": review_policy,
        }


def _validate_registry(
    root: Path,
    manifest: dict[str, Any],
    payloads: dict[str, object],
) -> None:
    files = manifest.get("rule_files")
    if not isinstance(files, list) or not files:
        raise RuleRegistryError("规则中心清单缺少 rule_files")

    categories = [entry.get("category") for entry in files if isinstance(entry, dict)]
    required = manifest.get("required_categories")
    if not isinstance(required, list) or set(categories) != set(required):
        raise RuleRegistryError("规则中心分类与必备分类不一致")
    keys = [entry.get("key") for entry in files if isinstance(entry, dict)]
    if len(keys) != len(set(keys)):
        raise RuleRegistryError("规则中心文件键重复")

    statement = payloads.get("statement_items")
    if not isinstance(statement, dict) or not isinstance(
        statement.get("statement_items"), list
    ):
        raise RuleRegistryError("正表项目规则缺少 statement_items")
    items = statement["statement_items"]
    item_ids = [item.get("item_id") for item in items if isinstance(item, dict)]
    if len(items) != 35 or len(item_ids) != 35 or len(set(item_ids)) != 35:
        raise RuleRegistryError("一般企业正表项目必须恰好包含35个唯一项目")
    known_items = set(item_ids)
    for item in items:
        direction = item.get("normal_direction")
        if direction not in {"inflow", "outflow", "net"}:
            raise RuleRegistryError(
                f"现金方向非法：{item.get('item_id')}={direction}"
            )
        for part in item.get("formula_components", []):
            if not isinstance(part, list) or len(part) != 2 or part[0] not in known_items:
                raise RuleRegistryError(
                    f"正表公式引用未知项目：{item.get('item_id')}={part}"
                )

    for payload in payloads.values():
        for item_id in _walk_candidate_item_ids(payload):
            if item_id not in known_items:
                raise RuleRegistryError(f"规则引用未知正表项目：{item_id}")
        for item_id in _walk_single_item_ids(payload):
            if item_id not in known_items:
                raise RuleRegistryError(f"规则引用未知正表项目：{item_id}")

    rule_ids: set[str] = set()
    for payload in payloads.values():
        for metadata in _walk_rule_metadata(payload):
            rule_id = str(metadata["rule_id"])
            if rule_id in rule_ids:
                raise RuleRegistryError(f"规则编号重复：{rule_id}")
            rule_ids.add(rule_id)
            if "status" in metadata and metadata["status"] not in {
                "active",
                "deprecated",
            }:
                raise RuleRegistryError(f"规则状态非法：{rule_id}")

    explicit_priorities: set[tuple[str, int]] = set()
    for payload in payloads.values():
        for metadata in _walk_rule_metadata(payload):
            if "category" not in metadata or "priority" not in metadata:
                continue
            key = (str(metadata["category"]), int(metadata["priority"]))
            if key in explicit_priorities:
                raise RuleRegistryError(
                    f"同一规则类别存在重复优先级：{key[0]}/{key[1]}"
                )
            explicit_priorities.add(key)

    evidence = payloads.get("evidence_and_actions")
    if not isinstance(evidence, dict):
        raise RuleRegistryError("缺少证据评分与行动规则")
    active_actions = set(evidence.get("active_actions", []))
    deprecated_actions = set(manifest.get("deprecated_actions", []))
    if active_actions.intersection(deprecated_actions):
        raise RuleRegistryError("已废止动作仍在启用动作清单中")
    allowed_scores = tuple(evidence.get("allowed_scores", []))
    registry_stub = RuleRegistry(
        root,
        manifest,
        payloads,
        frozenset(categories),
        frozenset(known_items),
        (),
        frozenset(active_actions),
        frozenset(deprecated_actions),
        allowed_scores,
        tuple(evidence.get("automatic_change_score_options", [])),
        int(evidence.get("default_automatic_change_score", 0)),
        (),
        "",
    )
    for group in ("agrees", "valid_original", "blank"):
        for score in allowed_scores:
            for level in ("M0", "M1", "M2", "M3"):
                registry_stub.normal_action_cell(group, int(score), level)
    for group_name in (
        "automatic",
        "ai_pending",
        "blind_multi_review",
        "human_pending",
        "all_pending",
    ):
        registry_stub.action_group(group_name)
    if not registry_stub.action_group("automatic").isdisjoint(
        registry_stub.action_group("all_pending")
    ):
        raise RuleRegistryError("自动动作和待处理动作不得重叠")
    unresolved_action = evidence.get("ai_review_outcomes", {}).get(
        "unresolved_action"
    )
    if unresolved_action not in active_actions:
        raise RuleRegistryError("AI未决出口引用未启用动作")

    output = payloads.get("workbook_output")
    if not isinstance(output, dict):
        raise RuleRegistryError("缺少工作簿输出规则")
    sheet_names = output.get("sheet_names")
    if (
        not isinstance(sheet_names, list)
        or len(sheet_names) != 14
        or len(set(sheet_names)) != 14
        or sheet_names[4] != "低金额系统兜底明细"
        or sheet_names[12] != "分类汇总视图"
        or "低金额人工批量" in sheet_names
    ):
        raise RuleRegistryError("最终工作簿必须是规定的14张工作表")
    summary_view = output.get("summary_view")
    if (
        not isinstance(summary_view, dict)
        or summary_view.get("sheet_name") != "分类汇总视图"
        or not summary_view.get("columns")
        or not summary_view.get("check_row_rule")
        or summary_view.get("source_columns")
        != {"item": "最终决定项目", "account": "标准一级科目", "amount": "本行分配现金变化"}
    ):
        raise RuleRegistryError("工作簿输出规则缺少分类汇总视图结构")
    if output.get("fallback_default_hidden_headers") != [
        "人工依据",
        "外部资料位置",
        "处理人",
        "处理时间",
    ]:
        raise RuleRegistryError("工作簿输出规则缺少兜底明细表默认隐藏列登记")
    if set(output.get("materiality_labels", {})) != {"M0", "M1", "M2", "M3"}:
        raise RuleRegistryError("工作簿输出规则缺少四档重要性中文名称")
    quality_labels = output.get("quality_labels", {})
    if any(
        set(quality_labels.get(kind, {})) != {"0", "10", "25", "45"}
        for kind in ("difference", "trace")
    ):
        raise RuleRegistryError("工作簿输出规则缺少四档证据质量中文名称")

    special = payloads.get("special_and_reconciliation")
    if not isinstance(special, dict) or special.get("difference_source_order") != [
        "account_path",
        "summary",
    ]:
        raise RuleRegistryError("差异表独立来源顺序必须固定为完整路径、摘要")


def load_rule_registry(project_root: Path) -> RuleRegistry:
    root = Path(project_root).resolve()
    references = root / "references"
    manifest_path = references / "规则中心清单.json"
    manifest = _read_json(manifest_path)
    payloads: dict[str, object] = {}
    digest = hashlib.sha256()
    digest.update(manifest_path.name.encode("utf-8"))
    digest.update(manifest_path.read_bytes())
    files = manifest.get("rule_files")
    if not isinstance(files, list):
        raise RuleRegistryError("规则中心清单缺少 rule_files")
    for entry in files:
        if not isinstance(entry, dict):
            raise RuleRegistryError("rule_files 每项必须是对象")
        key = str(entry.get("key", ""))
        path = references / str(entry.get("path", ""))
        if not key or not path.is_file():
            raise RuleRegistryError(f"规则中心登记文件不存在：{path.name}")
        payloads[key] = (
            _read_json(path) if entry.get("format") == "json" else path.read_text(encoding="utf-8-sig")
        )
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())

    _validate_registry(root, manifest, payloads)
    evidence = payloads["evidence_and_actions"]
    special = payloads["special_and_reconciliation"]
    output = payloads["workbook_output"]
    assert isinstance(evidence, dict) and isinstance(special, dict) and isinstance(output, dict)
    return RuleRegistry(
        project_root=root,
        manifest=manifest,
        payloads=payloads,
        categories=frozenset(
            str(entry["category"]) for entry in files if isinstance(entry, dict)
        ),
        statement_item_ids=frozenset(
            str(item["item_id"])
            for item in payloads["statement_items"]["statement_items"]  # type: ignore[index]
        ),
        output_sheet_names=tuple(str(value) for value in output["sheet_names"]),
        active_actions=frozenset(str(value) for value in evidence["active_actions"]),
        deprecated_actions=frozenset(
            str(value) for value in manifest.get("deprecated_actions", [])
        ),
        allowed_scores=tuple(int(value) for value in evidence["allowed_scores"]),
        automatic_change_score_options=tuple(
            int(value) for value in evidence["automatic_change_score_options"]
        ),
        default_automatic_change_score=int(evidence["default_automatic_change_score"]),
        difference_source_order=tuple(
            str(value) for value in special["difference_source_order"]
        ),
        fingerprint=digest.hexdigest(),
    )


def default_rule_registry() -> RuleRegistry:
    return load_rule_registry(Path(__file__).resolve().parents[2])
