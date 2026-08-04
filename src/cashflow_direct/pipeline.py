from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string

from cashflow_direct.ai_review import (
    AIResult,
    build_adjudication_tasks,
    resolve_automatic_decisions,
    select_ai_tasks,
    validate_ai_results,
)
from cashflow_direct.classification import classify_all, load_rule_pack
from cashflow_direct.components import (
    CashScope,
    build_cashflow_components,
    confirm_cash_scope as make_cash_scope,
    discover_cash_scope,
)
from cashflow_direct.duplicates import assign_duplicate_items, find_suspected_duplicates
from cashflow_direct.intake import register_inputs, validate_materiality
from cashflow_direct.models import (
    CashflowComponent,
    ClassificationDecision,
    AITask,
    MaterialityAmounts,
    NormalizedEntry,
    SourceLocator,
)
from cashflow_direct.money import stable_id, yuan_to_cent
from cashflow_direct.normalization import normalize_dataset
from cashflow_direct.semantic_mapping import DatasetMapping, MappingQuestion, infer_dataset_mapping
from cashflow_direct.statement import (
    ExistingStatementResult,
    aggregate_statement,
    compare_statement,
    parse_existing_statement,
    reconcile_cash,
)
from cashflow_direct.storage import RunStore
from cashflow_direct.validation import (
    validate_classification,
    validate_final_output,
    validate_input_hashes,
    validate_statement,
)
from cashflow_direct.workbook_output import WorkbookModel, build_output_workbook
from cashflow_direct.workbook_structure import scan_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRACE_DIR_NAME = "计算留痕数据"
STATE_FILE_NAME = "运行状态.json"
DB_FILE_NAME = "计算留痕.sqlite3"


@dataclass(frozen=True, slots=True)
class PreflightResult:
    run_id: str
    run_dir: Path
    status: str
    recommended_cash_decisions: dict[str, str]
    mapping_question_count: int
    source_entry_count: int


@dataclass(frozen=True, slots=True)
class StageResult:
    run_id: str
    run_dir: Path
    stage: str
    status: str
    next_action: str


@dataclass(frozen=True, slots=True)
class ClassificationStageResult:
    run_id: str
    run_dir: Path
    component_count: int
    component_hash: str
    source_entry_count: int
    cash_delta_cent: int
    ai_tasks_missing: int
    status: str


@dataclass(frozen=True, slots=True)
class AIStageResult:
    run_id: str
    run_dir: Path
    valid_count: int
    missing_count: int
    status: str


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    run_id: str
    run_dir: Path
    workbook_path: Path
    overall_status: str


def _trace_dir(run_dir: Path) -> Path:
    return Path(run_dir) / TRACE_DIR_NAME


def _state_path(run_dir: Path) -> Path:
    return _trace_dir(run_dir) / STATE_FILE_NAME


def _store(run_dir: Path) -> RunStore:
    return RunStore(_trace_dir(run_dir) / DB_FILE_NAME)


def _load_state(run_dir: Path) -> dict[str, object]:
    path = _state_path(run_dir)
    if not path.is_file():
        raise RuntimeError("未找到运行状态，请先执行资料预检")
    with path.open("r", encoding="utf-8-sig") as source:
        return json.load(source)


def _save_state(run_dir: Path, state: Mapping[str, object]) -> None:
    target = _state_path(run_dir)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="\n") as output:
        json.dump(state, output, ensure_ascii=False, separators=(",", ":"))
    temporary.replace(target)


def _write_trace_jsonl(run_dir: Path, filename: str, records: Sequence[object]) -> None:
    target = _trace_dir(run_dir) / filename
    with target.open("w", encoding="utf-8-sig", newline="\n") as output:
        for record in records:
            payload = asdict(record) if hasattr(record, "__dataclass_fields__") else record
            output.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")


def _entry_from_dict(payload: Mapping[str, object]) -> NormalizedEntry:
    data = dict(payload)
    data["source"] = SourceLocator(**data["source"])
    return NormalizedEntry(**data)


def _component_from_dict(payload: Mapping[str, object]) -> CashflowComponent:
    data = dict(payload)
    for key in ("counterpart_accounts", "source_keys", "anomalies", "source_file_ids"):
        data[key] = tuple(data.get(key, ()))
    return CashflowComponent(**data)


def _decision_from_dict(payload: Mapping[str, object]) -> ClassificationDecision:
    data = dict(payload)
    data["excluded_conflict_rule_ids"] = tuple(data.get("excluded_conflict_rule_ids", ()))
    return ClassificationDecision(**data)


def _scope_from_dict(payload: Mapping[str, object]) -> CashScope:
    return CashScope(
        frozenset(payload["included_keys"]),
        frozenset(payload["excluded_keys"]),
        tuple((item[0], tuple(item[1])) for item in payload["account_names_by_key"]),
        str(payload["scope_hash"]),
    )


def _materiality_from_state(state: Mapping[str, object]) -> MaterialityAmounts:
    return MaterialityAmounts(**state["materiality"])


def _assert_inputs_unchanged(state: Mapping[str, object]) -> None:
    result = validate_input_hashes(state["files"])
    if not result.valid:
        raise RuntimeError("输入文件已被修改，请建立新运行目录后重新处理")


def _read_cash_balances(path: Path) -> dict[str, tuple[int, int]]:
    found: dict[str, tuple[int, int]] = {}
    workbook = load_workbook(path, read_only=True, data_only=True, keep_vba=False)
    try:
        for sheet in workbook.worksheets:
            priority = 2 if "余额" in sheet.title else 1
            for row in sheet.iter_rows(min_row=1, max_row=100, max_col=10, values_only=True):
                for index, value in enumerate(row[:-1]):
                    if not isinstance(value, str) or row[index + 1] in (None, ""):
                        continue
                    key = None
                    if "期初现金及现金等价物余额" in value:
                        key = "opening_cent"
                    elif "期末现金及现金等价物余额" in value:
                        key = "closing_cent"
                    elif "汇率变动对现金及现金等价物的影响" in value:
                        key = "fx_cent"
                    if key is not None:
                        try:
                            amount = yuan_to_cent(row[index + 1])
                            if key not in found or priority > found[key][0]:
                                found[key] = (priority, amount)
                        except ValueError:
                            continue
    finally:
        workbook.close()
    return found


def _result_from_classification(state: Mapping[str, object], run_dir: Path) -> ClassificationStageResult:
    data = state["classification_summary"]
    return ClassificationStageResult(
        str(state["run_id"]),
        Path(run_dir),
        int(data["component_count"]),
        str(data["component_hash"]),
        int(data["source_entry_count"]),
        int(data["cash_delta_cent"]),
        int(data["ai_tasks_missing"]),
        str(data["status"]),
    )


def run_preflight(
    inputs: Sequence[Path],
    materiality: tuple[object, object, object],
    output_parent: Path | None = None,
) -> PreflightResult:
    amounts = validate_materiality(*materiality)
    intake = register_inputs(inputs, output_parent=output_parent)
    run_dir = intake.run_dir
    trace_dir = _trace_dir(run_dir)
    trace_dir.mkdir(parents=True, exist_ok=True)
    store = _store(run_dir)
    store.initialize()
    rules = load_rule_pack(PROJECT_ROOT)

    entries: list[NormalizedEntry] = []
    mappings: list[dict[str, object]] = []
    questions: list[dict[str, object]] = []
    existing_statement_path: str | None = None
    balance_candidates: dict[str, tuple[int, int]] = {}
    for registered in intake.active_files:
        for key, candidate in _read_cash_balances(registered.path).items():
            if key not in balance_candidates or candidate[0] > balance_candidates[key][0]:
                balance_candidates[key] = candidate
        snapshot = scan_workbook(registered.path)
        mapping = infer_dataset_mapping(snapshot)
        if isinstance(mapping, DatasetMapping):
            normalized = normalize_dataset(registered.path, registered.file_id, mapping)
            entries.extend(normalized.entries)
            mappings.append(
                {
                    "file_id": registered.file_id,
                    "file": registered.path.name,
                    "sheet": mapping.sheet_name,
                    "header_rows": f"{mapping.header_row_start}:{mapping.header_row_end}",
                    "roles": {role: column.column_letter for role, column in mapping.role_to_column.items()},
                }
            )
            continue
        existing = parse_existing_statement(registered.path, rules)
        if isinstance(existing, ExistingStatementResult):
            existing_statement_path = str(registered.path)
            continue
        questions.append(
            {
                "file_id": registered.file_id,
                "file": registered.path.name,
                "role": mapping.role,
                "recommended": mapping.recommended.column_letter,
            }
        )

    proposal = discover_cash_scope(entries)
    recommended = {
        candidate.account_key: candidate.system_suggestion
        for candidate in proposal.candidates
        if candidate.system_suggestion in {"include", "exclude"}
    }
    run_id = stable_id("RUN", run_dir.name, *(item.sha256 for item in intake.files))
    state: dict[str, object] = {
        "schema_version": "1.0",
        "run_id": run_id,
        "stage": "preflight",
        "files": [
            {
                "file_id": item.file_id,
                "path": str(item.path),
                "sha256": item.sha256,
                "duplicate_of": item.duplicate_of,
                "is_macro_workbook": item.is_macro_workbook,
            }
            for item in intake.files
        ],
        "materiality": asdict(amounts),
        "entries": [asdict(entry) for entry in entries],
        "mappings": mappings,
        "mapping_questions": questions,
        "cash_scope_proposal": asdict(proposal),
        "recommended_cash_decisions": recommended,
        "existing_statement_path": existing_statement_path,
        "cash_balances": {key: value for key, (_, value) in balance_candidates.items()},
    }
    with store.stage("preflight") as connection:
        connection.execute(
            "INSERT INTO run_manifest(record_id, payload_json) VALUES (?, ?)",
            (run_id, json.dumps({"materiality": asdict(amounts)}, ensure_ascii=False)),
        )
        connection.executemany(
            "INSERT INTO source_entry(record_id, payload_json) VALUES (?, ?)",
            ((entry.entry_id, json.dumps(asdict(entry), ensure_ascii=False)) for entry in entries),
        )
    _save_state(run_dir, state)
    status = "waiting_mapping" if questions else "waiting_cash_scope"
    return PreflightResult(run_id, run_dir, status, recommended, len(questions), len(entries))


def confirm_mapping(run_dir: Path, decisions: Mapping[str, str]) -> StageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if not state.get("mapping_questions"):
        return StageResult(str(state["run_id"]), Path(run_dir), "mapping", "completed", "确认现金范围")
    confirmations = dict(state.get("mapping_confirmations", {}))
    confirmations.update(decisions)
    files_by_id = {str(item["file_id"]): Path(str(item["path"])) for item in state["files"]}
    pending_by_file: dict[str, list[dict[str, object]]] = {}
    for question in state["mapping_questions"]:
        pending_by_file.setdefault(str(question["file_id"]), []).append(question)

    new_questions: list[dict[str, object]] = []
    new_entries: list[NormalizedEntry] = []
    new_mappings: list[dict[str, object]] = []
    for file_id, questions in pending_by_file.items():
        prefix = f"{file_id}:"
        overrides: dict[str, int] = {
            key[len(prefix) :]: column_index_from_string(str(value).strip().upper())
            for key, value in confirmations.items()
            if key.startswith(prefix)
        }
        for question in questions:
            role = str(question["role"])
            key = f"{file_id}:{role}"
            choice = confirmations.get(key)
            if not choice:
                raise ValueError(f"等待字段确认：缺少 {key}")
            try:
                overrides[role] = column_index_from_string(str(choice).strip().upper())
            except ValueError as error:
                raise ValueError(f"字段 {key} 的确认值必须是 Excel 列字母") from error
        path = files_by_id[file_id]
        mapping = infer_dataset_mapping(scan_workbook(path), overrides)
        if isinstance(mapping, MappingQuestion):
            new_questions.append(
                {
                    "file_id": file_id,
                    "file": path.name,
                    "role": mapping.role,
                    "recommended": mapping.recommended.column_letter,
                }
            )
            continue
        normalized = normalize_dataset(path, file_id, mapping)
        new_entries.extend(normalized.entries)
        new_mappings.append(
            {
                "file_id": file_id,
                "file": path.name,
                "sheet": mapping.sheet_name,
                "header_rows": f"{mapping.header_row_start}:{mapping.header_row_end}",
                "roles": {role: column.column_letter for role, column in mapping.role_to_column.items()},
            }
        )

    entries = [_entry_from_dict(item) for item in state["entries"]] + new_entries
    proposal = discover_cash_scope(entries)
    state["entries"] = [asdict(item) for item in entries]
    state["mappings"] = [*state.get("mappings", ()), *new_mappings]
    state["mapping_questions"] = new_questions
    state["mapping_confirmations"] = confirmations
    state["cash_scope_proposal"] = asdict(proposal)
    state["recommended_cash_decisions"] = {
        candidate.account_key: candidate.system_suggestion
        for candidate in proposal.candidates
        if candidate.system_suggestion in {"include", "exclude"}
    }
    state["stage"] = "waiting_mapping" if new_questions else "waiting_cash_scope"
    if new_entries:
        with _store(run_dir).stage("mapping") as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO source_entry(record_id, payload_json) VALUES (?, ?)",
                ((entry.entry_id, json.dumps(asdict(entry), ensure_ascii=False)) for entry in new_entries),
            )
    _save_state(run_dir, state)
    return StageResult(
        str(state["run_id"]),
        Path(run_dir),
        "mapping",
        "waiting" if new_questions else "completed",
        "继续确认剩余字段" if new_questions else "确认现金范围",
    )


def confirm_cash_scope(
    run_dir: Path,
    decisions: Mapping[str, str],
) -> StageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if state.get("mapping_questions"):
        raise RuntimeError("字段映射仍有待确认项，请先完成字段确认")
    entries = tuple(_entry_from_dict(item) for item in state["entries"])
    scope = make_cash_scope(discover_cash_scope(entries), decisions)
    store = _store(run_dir)
    with store.stage("cash_scope") as connection:
        connection.execute(
            "INSERT OR REPLACE INTO cash_scope(record_id, payload_json) VALUES (?, ?)",
            (scope.scope_hash, json.dumps(asdict(scope), ensure_ascii=False, default=list)),
        )
    state["cash_scope"] = {
        "included_keys": sorted(scope.included_keys),
        "excluded_keys": sorted(scope.excluded_keys),
        "account_names_by_key": scope.account_names_by_key,
        "scope_hash": scope.scope_hash,
    }
    state["stage"] = "cash_scope_confirmed"
    _save_state(run_dir, state)
    return StageResult(str(state["run_id"]), Path(run_dir), "cash_scope", "completed", "执行自动分类")


def run_classification(run_dir: Path) -> ClassificationStageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if "classification_summary" in state:
        return _result_from_classification(state, run_dir)
    if "cash_scope" not in state:
        raise RuntimeError("请确认现金范围后继续")
    entries = tuple(_entry_from_dict(item) for item in state["entries"])
    scope = _scope_from_dict(state["cash_scope"])
    build = build_cashflow_components(entries, scope)
    entry_by_id = {entry.entry_id: entry for entry in entries}
    components = tuple(
        replace(
            component,
            voucher_date=next((entry_by_id[key].voucher_date for key in component.source_keys if key in entry_by_id), ""),
            voucher_no=next((entry_by_id[key].voucher_no for key in component.source_keys if key in entry_by_id), ""),
            source_file_ids=tuple(
                sorted({entry_by_id[key].source.file_id for key in component.source_keys if key in entry_by_id})
            ),
        )
        for component in build.components
    )
    rules = load_rule_pack(PROJECT_ROOT)
    decisions = classify_all(components, rules)
    checked = validate_classification(components, decisions)
    if not checked.valid:
        raise RuntimeError("自动分类不变量失败：" + "；".join(checked.errors))
    tasks = select_ai_tasks(components, decisions, _materiality_from_state(state))
    serialized_components = [asdict(item) for item in components]
    serialized_decisions = [asdict(item) for item in decisions]
    digest_source = json.dumps(serialized_components, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    component_hash = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()
    summary = {
        "component_count": len(components),
        "component_hash": component_hash,
        "source_entry_count": len(entries),
        "cash_delta_cent": sum(item.cash_delta_cent for item in components),
        "ai_tasks_missing": len(tasks),
        "status": "waiting_ai" if tasks else "classification_completed",
    }
    store = _store(run_dir)
    with store.stage("classification") as connection:
        connection.executemany(
            "INSERT INTO cashflow_component(record_id, payload_json) VALUES (?, ?)",
            ((item.component_id, json.dumps(asdict(item), ensure_ascii=False)) for item in components),
        )
        connection.executemany(
            "INSERT INTO classification_decision(record_id, payload_json) VALUES (?, ?)",
            ((item.component_id, json.dumps(asdict(item), ensure_ascii=False)) for item in decisions),
        )
        connection.executemany(
            "INSERT INTO ai_task(record_id, payload_json) VALUES (?, ?)",
            ((item.task_id, json.dumps(asdict(item), ensure_ascii=False)) for item in tasks),
        )
    state["components"] = serialized_components
    state["decisions"] = serialized_decisions
    state["ai_tasks"] = [asdict(item) for item in tasks]
    state["classification_summary"] = summary
    state["stage"] = summary["status"]
    _write_trace_jsonl(run_dir, "AI复核请求.jsonl", tasks)
    _save_state(run_dir, state)
    return _result_from_classification(state, run_dir)


def import_ai_results(run_dir: Path, result_path: Path) -> AIStageResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    payloads = []
    with Path(result_path).open("r", encoding="utf-8-sig") as source:
        for line in source:
            if line.strip():
                payloads.append(json.loads(line))
    leaf_ids = {
        item.item_id for item in load_rule_pack(PROJECT_ROOT).statement_items if item.is_leaf
    }
    if state.get("adjudication_tasks") and state.get("stage") == "waiting_adjudication":
        tasks = tuple(
            AITask(
                task_id=str(item["task_id"]),
                component_id=str(item["component_id"]),
                context=str(item["context"]),
                original_item="",
                system_item_id=str(item["system_item_id"]),
                rule_evidence=f"AI 首次分类：{item['ai_item_id']}",
            )
            for item in state["adjudication_tasks"]
        )
        validation = validate_ai_results(tasks, payloads, leaf_ids)
        state["adjudication_validation"] = {
            "valid_results": [asdict(item) for item in validation.valid_results],
            "missing_ids": validation.missing_ids,
            "duplicate_ids": validation.duplicate_ids,
            "invalid_ids": validation.invalid_ids,
            "status": validation.status,
        }
        _write_trace_jsonl(run_dir, "AI裁决结果.jsonl", validation.valid_results)
        state["classification_summary"]["ai_tasks_missing"] = len(validation.missing_ids)
        if validation.status == "AI 已完成":
            system_decisions = tuple(_decision_from_dict(item) for item in state["system_decisions"])
            ai_results = tuple(AIResult(**item) for item in state["ai_validation"]["valid_results"])
            resolved = resolve_automatic_decisions(
                system_decisions, ai_results, validation.valid_results
            )
            item_by_id = load_rule_pack(PROJECT_ROOT).item_by_id
            resolved = tuple(
                replace(
                    decision,
                    system_item_name=item_by_id[decision.system_item_id].name,
                    normal_direction=item_by_id[decision.system_item_id].normal_direction,
                )
                if not decision.excluded
                else decision
                for decision in resolved
            )
            state["decisions"] = [asdict(item) for item in resolved]
            state["stage"] = "ai_completed"
        else:
            state["stage"] = "waiting_adjudication"
        _save_state(run_dir, state)
        return AIStageResult(
            str(state["run_id"]),
            Path(run_dir),
            len(validation.valid_results),
            len(validation.missing_ids),
            validation.status,
        )

    tasks = tuple(AITask(**item) for item in state.get("ai_tasks", ()))
    validation = validate_ai_results(tasks, payloads, leaf_ids)
    state["ai_validation"] = {
        "valid_results": [asdict(item) for item in validation.valid_results],
        "missing_ids": validation.missing_ids,
        "duplicate_ids": validation.duplicate_ids,
        "invalid_ids": validation.invalid_ids,
        "status": validation.status,
    }
    _write_trace_jsonl(run_dir, "AI复核结果.jsonl", validation.valid_results)
    if validation.status == "AI 已完成":
        system_decisions = tuple(_decision_from_dict(item) for item in state["decisions"])
        adjudication_tasks = build_adjudication_tasks(system_decisions, validation.valid_results)
        if adjudication_tasks:
            state["system_decisions"] = [asdict(item) for item in system_decisions]
            state["adjudication_tasks"] = [asdict(item) for item in adjudication_tasks]
            _write_trace_jsonl(run_dir, "AI裁决请求.jsonl", adjudication_tasks)
            state["classification_summary"]["ai_tasks_missing"] = len(adjudication_tasks)
            state["stage"] = "waiting_adjudication"
            status = "AI 待裁决"
            missing_count = len(adjudication_tasks)
        else:
            state["classification_summary"]["ai_tasks_missing"] = 0
            state["stage"] = "ai_completed"
            status = validation.status
            missing_count = 0
    else:
        state["classification_summary"]["ai_tasks_missing"] = len(validation.missing_ids)
        state["stage"] = "waiting_ai"
        status = validation.status
        missing_count = len(validation.missing_ids)
    _save_state(run_dir, state)
    return AIStageResult(
        str(state["run_id"]), Path(run_dir), len(validation.valid_results), missing_count, status
    )


def finalize_run(run_dir: Path) -> FinalizeResult:
    state = _load_state(run_dir)
    _assert_inputs_unchanged(state)
    if state.get("overall_status") and state.get("workbook_path"):
        return FinalizeResult(
            str(state["run_id"]), Path(run_dir), Path(state["workbook_path"]), str(state["overall_status"])
        )
    if "classification_summary" not in state:
        raise RuntimeError("请先完成自动分类")
    if int(state["classification_summary"]["ai_tasks_missing"]) > 0:
        raise RuntimeError("AI 复核尚未逐编号完成，不能生成最终结果")

    components = tuple(_component_from_dict(item) for item in state["components"])
    decisions = tuple(_decision_from_dict(item) for item in state["decisions"])
    rules = load_rule_pack(PROJECT_ROOT)
    statement = aggregate_statement(components, decisions, rules)
    statement_check = validate_statement(statement)
    if not statement_check.valid:
        raise RuntimeError("正表金额勾稽失败：" + "；".join(statement_check.errors))
    comparison = None
    existing_path = state.get("existing_statement_path")
    if existing_path:
        existing = parse_existing_statement(Path(str(existing_path)), rules)
        if isinstance(existing, MappingQuestion):
            raise RuntimeError("客户现有正表仍有无法映射的项目，请先确认")
        comparison = compare_statement(existing, statement)

    balances = state.get("cash_balances", {})
    reconciliation = reconcile_cash(
        statement,
        balances.get("opening_cent"),
        balances.get("closing_cent"),
        balances.get("fx_cent"),
    )
    duplicate_groups = assign_duplicate_items(
        find_suspected_duplicates(
            components, _materiality_from_state(state).performance_cent
        ),
        decisions,
    )
    trace_rows = tuple(
        {
            "业务组成编号": component.component_id,
            "摘要": component.summary,
            "现金变化": component.cash_delta_cent / 100,
            "系统项目": decision.system_item_id,
            "命中规则": decision.matched_rule_id,
            "判断理由": decision.reason,
            "来源占用键": "、".join(component.source_keys),
            "异常": "、".join(component.anomalies),
        }
        for component, decision in zip(components, decisions, strict=True)
    )
    mapping_rows = tuple(
        {
            "文件": item["file"],
            "工作表": item["sheet"],
            "表头行": item["header_rows"],
            "字段映射": json.dumps(item["roles"], ensure_ascii=False),
        }
        for item in state.get("mappings", ())
    )
    status = (
        "final_usable"
        if reconciliation.status == "现金调节完成" and not any(group.blocks_manual_completion for group in duplicate_groups)
        else "draft_cash_reconciliation_incomplete"
    )
    model = WorkbookModel(
        statement=statement,
        rules=rules,
        comparison=comparison,
        review_batches=(),
        duplicate_groups=duplicate_groups,
        ai_records=tuple(state.get("ai_validation", {}).get("valid_results", ())),
        cash_scope_rows=tuple(
            {"科目": key, "决定": "纳入"}
            for key in state["cash_scope"]["included_keys"]
        ),
        reconciliation=reconciliation,
        trace_rows=trace_rows,
        mapping_rows=mapping_rows,
        overall_status=status,
    )
    workbook_path = Path(run_dir) / "现金流量表正表及复核底稿.xlsx"
    build_output_workbook(model, workbook_path)
    output_check = validate_final_output(workbook_path, model)
    if not output_check.valid:
        status = "output_validation_failed"
    store = _store(run_dir)
    with store.stage("finalize") as connection:
        connection.executemany(
            "INSERT OR REPLACE INTO statement_value(record_id, payload_json) VALUES (?, ?)",
            (
                (item_id, json.dumps({"amount_cent": amount}, ensure_ascii=False))
                for item_id, amount in statement.values.items()
            ),
        )
    state["reconciliation"] = asdict(reconciliation)
    state["workbook_path"] = str(workbook_path)
    state["overall_status"] = status
    state["stage"] = "finalized"
    _save_state(run_dir, state)
    return FinalizeResult(str(state["run_id"]), Path(run_dir), workbook_path, status)
