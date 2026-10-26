from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.串行上下文调度 import (
    DispatchError,
    complete_dispatch,
    handoff,
    initialize,
    recover_dispatch,
    sha256_file,
    start_dispatch,
    status,
)


def test_1m_bypasses_and_128k_rejects_second_active_task(tmp_path: Path):
    backup = tmp_path / "backup"
    assert initialize(tmp_path, "1M", "current-model", backup)["status"] == "bypassed"
    assert not (tmp_path / "计算留痕数据" / "上下文调度状态.json").exists()

    state = initialize(tmp_path, "128K", "current-model", backup)
    assert state["context_mode"] == "128K"
    packet = tmp_path / "计算留痕数据" / "AI复核请求_第01批.jsonl"
    packet.write_text('{"task_id":"AI-1"}\n', encoding="utf-8-sig")
    original_hash = sha256_file(packet)
    start_dispatch(tmp_path, "AI复核请求_第01批", packet, "window-A", backup)
    with pytest.raises(DispatchError, match="活动任务"):
        start_dispatch(tmp_path, "AI复核请求_第02批", packet, "window-B", backup)
    assert sha256_file(packet) == original_hash


def _initialized_run(root: Path) -> Path:
    trace = root / "计算留痕数据"
    trace.mkdir(parents=True, exist_ok=True)
    (trace / "运行状态.json").write_text(
        '{"stage":"waiting_ai"}', encoding="utf-8-sig"
    )
    initialize(root, "128K", "current-model", root / "backup")
    return root


def _packet(run_dir: Path) -> Path:
    packet = run_dir / "计算留痕数据" / "AI复核请求_第01批.jsonl"
    packet.write_text('{"task_id":"AI-1"}\n', encoding="utf-8-sig")
    return packet


def test_completion_requires_matching_active_task_and_rotates_at_20(tmp_path: Path):
    run_dir = _initialized_run(tmp_path)
    packet = _packet(run_dir)
    for index in range(20):
        task_id = f"task-{index}"
        start_dispatch(run_dir, task_id, packet, f"window-{index}", tmp_path / "backup")
        result = tmp_path / f"result-{index}.jsonl"
        result.write_text("{}\n", encoding="utf-8-sig")
        complete_dispatch(run_dir, task_id, result, packet, tmp_path / "backup")
    assert status(run_dir)["rotation_required"] is True
    assert status(run_dir)["active_dispatch"] is None
    assert "tasks" not in status(run_dir)
    assert "components" not in status(run_dir)


def test_recovery_clears_only_scheduler_occupancy(tmp_path: Path):
    run_dir = _initialized_run(tmp_path)
    packet = _packet(run_dir)
    start_dispatch(run_dir, "task-A", packet, "window-A", tmp_path / "backup")
    recovered = recover_dispatch(run_dir, "task-A", "窗口异常退出", tmp_path / "backup")
    assert recovered["active_dispatch"] is None
    assert json.loads((run_dir / "计算留痕数据" / "运行状态.json").read_text(encoding="utf-8-sig"))["stage"] == "waiting_ai"


def test_completion_rejects_mismatched_task_id(tmp_path: Path):
    run_dir = _initialized_run(tmp_path)
    packet = _packet(run_dir)
    start_dispatch(run_dir, "task-A", packet, "window-A", tmp_path / "backup")
    result = tmp_path / "result.jsonl"
    result.write_text("{}\n", encoding="utf-8-sig")
    with pytest.raises(DispatchError, match="任务编号"):
        complete_dispatch(run_dir, "task-B", result, packet, tmp_path / "backup")
    assert status(run_dir)["active_dispatch"] is not None


def test_handoff_resets_rotation_and_returns_summary(tmp_path: Path):
    run_dir = _initialized_run(tmp_path)
    packet = _packet(run_dir)
    start_dispatch(run_dir, "task-A", packet, "window-A", tmp_path / "backup")
    result = tmp_path / "result.jsonl"
    result.write_text("{}\n", encoding="utf-8-sig")
    complete_dispatch(run_dir, "task-A", result, packet, tmp_path / "backup")
    info = handoff(run_dir)
    assert set(info) == {"context_mode", "model_binding", "active_dispatch", "business_anchor", "rotation_required"}
    assert status(run_dir)["controller_dispatch_count"] == 0
    assert status(run_dir)["rotation_required"] is False


def test_router_contract_and_original_packet_hash(tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    skill = (root / "SKILL.md").read_text(encoding="utf-8-sig")
    guide = (root / "references" / "128K串行上下文.md").read_text(encoding="utf-8-sig")
    assert "本次使用 1M 还是 128K 上下文模型" in skill
    assert "前台" in guide and "串行" in guide
    assert "不得" in guide and "改写原任务" in guide
    assert "C" in guide and "A/B" in guide
    assert "result_sha256" in guide
    packet = tmp_path / "AI后续复核请求_第01批.jsonl"
    packet.write_text('{"task_id":"A","context":"；独立复核A："}\n{"task_id":"B","context":"；独立复核B："}\n', encoding="utf-8-sig")
    before = sha256_file(packet)
    initialize(tmp_path, "128K", "current-model", tmp_path / "backup")
    start_dispatch(tmp_path, "review-A", packet, "window-A", tmp_path / "backup")
    assert sha256_file(packet) == before


def test_cli_subcommands_dispatch(tmp_path: Path, capsys):
    from scripts.串行上下文调度 import main

    backup = tmp_path / "backup"
    base = ["--run-dir", str(tmp_path)]
    assert main(["初始化", *base, "--mode", "128K", "--model-binding", "current-model", "--backup-root", str(backup)]) == 0
    packet = _packet(tmp_path)
    assert main(["开始", *base, "--task-id", "T1", "--packet", str(packet), "--window-id", "W1", "--backup-root", str(backup)]) == 0
    result = tmp_path / "result.jsonl"
    result.write_text("{}\n", encoding="utf-8-sig")
    assert main(["完成", *base, "--task-id", "T1", "--result", str(result), "--business-anchor", str(packet), "--backup-root", str(backup)]) == 0
    assert main(["状态", *base]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["active_dispatch"] is None
    assert summary["controller_dispatch_count"] == 1
    assert main(["开始", *base, "--task-id", "T2", "--packet", str(packet), "--window-id", "W2", "--backup-root", str(backup)]) == 0
    assert main(["恢复", *base, "--task-id", "T2", "--reason", "测试恢复", "--backup-root", str(backup)]) == 0
    assert main(["交接", *base]) == 0
    assert json.loads(capsys.readouterr().out)["context_mode"] == "128K"
