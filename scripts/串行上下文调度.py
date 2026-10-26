"""128K 串行上下文窗口调度外壳。

只管理调度占用、哈希与短状态，不是业务真相：任务完成与否以运行状态、
SQLite 和原 JSONL 为准。本模块不得导入任何 cashflow_direct 业务模块。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Sequence


class DispatchError(RuntimeError):
    """调度状态不允许当前动作。"""


STATE_NAME = "上下文调度状态.json"
LOG_NAME = "上下文调度记录.jsonl"
TRACE_DIR_NAME = "计算留痕数据"
CHUNK_SIZE = 1 << 20
CONTROLLER_DISPATCH_LIMIT = 20


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _trace_dir(run_dir: Path) -> Path:
    return Path(run_dir) / TRACE_DIR_NAME


def _state_path(run_dir: Path) -> Path:
    return _trace_dir(run_dir) / STATE_NAME


def _log_path(run_dir: Path) -> Path:
    return _trace_dir(run_dir) / LOG_NAME


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _backup_file(run_dir: Path, path: Path, backup_root: Path) -> None:
    """覆盖前把既有文件复制到备份目录并做字节比较，失败即停止。"""
    if not path.is_file():
        return
    target = Path(backup_root) / Path(path).relative_to(Path(run_dir))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    if target.read_bytes() != path.read_bytes():
        raise DispatchError(f"备份字节校验失败：{path.name}")


def _load_state_if_any(run_dir: Path) -> dict[str, Any] | None:
    path = _state_path(run_dir)
    if not path.is_file():
        return None
    state = json.loads(path.read_text(encoding="utf-8-sig"))
    if state.get("context_mode") not in ("1M", "128K"):
        raise DispatchError("调度状态文件损坏：context_mode 缺失或非法")
    return state


def _load_128k_state(run_dir: Path) -> dict[str, Any]:
    state = _load_state_if_any(run_dir)
    if state is None:
        raise DispatchError("尚未初始化 128K 调度状态")
    if state["context_mode"] != "128K":
        raise DispatchError("当前运行不是 128K 模式，禁止调度")
    return state


def _os_replace_retry(tmp: Path, path: Path) -> None:
    # Windows 下新文件可能被杀毒扫描短暂锁定，replace 偶发拒绝访问，需重试
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def _save_state_with_backup(run_dir: Path, state: dict[str, Any], backup_root: Path) -> None:
    path = _state_path(run_dir)
    _backup_file(run_dir, path, backup_root)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    _os_replace_retry(tmp, path)


def _append_log_with_backup(run_dir: Path, event: str, detail: dict[str, Any], backup_root: Path) -> None:
    path = _log_path(run_dir)
    _backup_file(run_dir, path, backup_root)
    line = json.dumps({"event": event, "time": _now(), "detail": detail}, ensure_ascii=False)
    data = line.encode("utf-8") + b"\n"
    if not path.is_file() or path.stat().st_size == 0:
        data = b"\xef\xbb\xbf" + data
    with open(path, "ab") as handle:
        handle.write(data)


def initialize(run_dir: Path, mode: str, model_binding: str, backup_root: Path) -> dict[str, Any]:
    mode = str(mode).strip()
    if mode == "1M":
        return {"status": "bypassed"}
    if mode != "128K":
        raise DispatchError(f"不支持的上下文模式：{mode}")
    _trace_dir(run_dir).mkdir(parents=True, exist_ok=True)
    existing = _load_state_if_any(run_dir)
    if existing is not None:
        return existing
    state: dict[str, Any] = {
        "context_mode": "128K",
        "model_binding": model_binding,
        "active_dispatch": None,
        "controller_dispatch_count": 0,
        "business_anchor": None,
        "created_at": _now(),
    }
    _save_state_with_backup(run_dir, state, backup_root)
    _append_log_with_backup(run_dir, "初始化", {"model_binding": model_binding}, backup_root)
    return state


def start_dispatch(run_dir: Path, task_id: str, packet: Path, window_id: str, backup_root: Path) -> dict[str, Any]:
    state = _load_128k_state(run_dir)
    if state["active_dispatch"] is not None:
        raise DispatchError("存在活动任务，禁止开始第二个任务")
    packet = Path(packet)
    if not packet.is_file():
        raise DispatchError("原任务文件不存在")
    state["active_dispatch"] = {
        "task_id": task_id,
        "window_id": window_id,
        "packet_path": str(packet),
        "packet_sha256": sha256_file(packet),
        "started_at": _now(),
    }
    _save_state_with_backup(run_dir, state, backup_root)
    _append_log_with_backup(run_dir, "start", state["active_dispatch"], backup_root)
    return state


def complete_dispatch(run_dir: Path, task_id: str, result: Path, business_anchor: Path, backup_root: Path) -> dict[str, Any]:
    """原 import 程序接受结果后才允许调用；这里只登记，不认定业务完成。"""
    state = _load_128k_state(run_dir)
    active = state["active_dispatch"]
    if active is None:
        raise DispatchError("没有活动任务可登记完成")
    if active["task_id"] != task_id:
        raise DispatchError(f"任务编号与活动任务不匹配：{task_id} ≠ {active['task_id']}")
    result = Path(result)
    if not result.is_file():
        raise DispatchError("结果文件不存在")
    business_anchor = Path(business_anchor)
    if not business_anchor.is_file():
        raise DispatchError("业务锚点文件不存在")
    detail = {
        "task_id": task_id,
        "window_id": active["window_id"],
        "result_path": str(result),
        "result_sha256": sha256_file(result),
        "business_anchor_path": str(business_anchor),
        "business_anchor_sha256": sha256_file(business_anchor),
    }
    state["active_dispatch"] = None
    state["business_anchor"] = {
        "path": detail["business_anchor_path"],
        "sha256": detail["business_anchor_sha256"],
    }
    state["controller_dispatch_count"] = int(state.get("controller_dispatch_count", 0)) + 1
    _save_state_with_backup(run_dir, state, backup_root)
    _append_log_with_backup(run_dir, "完成", detail, backup_root)
    return state


def recover_dispatch(run_dir: Path, task_id: str, reason: str, backup_root: Path) -> dict[str, Any]:
    """只解除调度占用并留痕，不修改运行状态、SQLite 或任何业务任务文件。"""
    state = _load_128k_state(run_dir)
    active = state["active_dispatch"]
    if active is None:
        raise DispatchError("没有活动任务占用，无需恢复")
    if active["task_id"] != task_id:
        raise DispatchError(f"任务编号与活动任务不匹配：{task_id} ≠ {active['task_id']}")
    state["active_dispatch"] = None
    _save_state_with_backup(run_dir, state, backup_root)
    _append_log_with_backup(run_dir, "恢复", {"task_id": task_id, "window_id": active["window_id"], "reason": reason}, backup_root)
    return state


def status(run_dir: Path) -> dict[str, Any]:
    """只返回调度摘要；不读入完整运行状态或任务正文。"""
    state = _load_128k_state(run_dir)
    count = int(state.get("controller_dispatch_count", 0))
    return {
        "context_mode": state["context_mode"],
        "model_binding": state.get("model_binding"),
        "active_dispatch": state.get("active_dispatch"),
        "business_anchor": state.get("business_anchor"),
        "controller_dispatch_count": count,
        "rotation_required": count >= CONTROLLER_DISPATCH_LIMIT,
    }


def handoff(run_dir: Path, backup_root: Path | None = None) -> dict[str, Any]:
    """生成新主控窗口的简短续作信息，并重置主控调度计数。"""
    state = _load_128k_state(run_dir)
    info = {
        "context_mode": state["context_mode"],
        "model_binding": state.get("model_binding"),
        "active_dispatch": state.get("active_dispatch"),
        "business_anchor": state.get("business_anchor"),
        "rotation_required": int(state.get("controller_dispatch_count", 0)) >= CONTROLLER_DISPATCH_LIMIT,
    }
    if backup_root is None:
        backup_root = Path(run_dir) / "backup"
    if state.get("controller_dispatch_count"):
        state["controller_dispatch_count"] = 0
        _save_state_with_backup(run_dir, state, backup_root)
        _append_log_with_backup(run_dir, "主控轮换", {"handed_off": True}, backup_root)
    return info


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="128K 串行上下文窗口调度")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_run_dir(name: str) -> argparse.ArgumentParser:
        child = sub.add_parser(name)
        child.add_argument("--run-dir", required=True)
        return child

    child = with_run_dir("初始化")
    child.add_argument("--mode", required=True)
    child.add_argument("--model-binding", default="")
    child.add_argument("--backup-root", required=True)
    child = with_run_dir("开始")
    child.add_argument("--task-id", required=True)
    child.add_argument("--packet", required=True)
    child.add_argument("--window-id", required=True)
    child.add_argument("--backup-root", required=True)
    child = with_run_dir("完成")
    child.add_argument("--task-id", required=True)
    child.add_argument("--result", required=True)
    child.add_argument("--business-anchor", required=True)
    child.add_argument("--backup-root", required=True)
    with_run_dir("状态")
    with_run_dir("交接")
    child = with_run_dir("恢复")
    child.add_argument("--task-id", required=True)
    child.add_argument("--reason", required=True)
    child.add_argument("--backup-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    run_dir = Path(args.run_dir)
    try:
        if args.command == "初始化":
            initialize(run_dir, args.mode, args.model_binding, Path(args.backup_root))
        elif args.command == "开始":
            start_dispatch(run_dir, args.task_id, Path(args.packet), args.window_id, Path(args.backup_root))
        elif args.command == "完成":
            complete_dispatch(run_dir, args.task_id, Path(args.result), Path(args.business_anchor), Path(args.backup_root))
        elif args.command == "状态":
            print(json.dumps(status(run_dir), ensure_ascii=False, indent=2))
        elif args.command == "交接":
            print(json.dumps(handoff(run_dir), ensure_ascii=False, indent=2))
        elif args.command == "恢复":
            recover_dispatch(run_dir, args.task_id, args.reason, Path(args.backup_root))
    except DispatchError as exc:
        print(f"ERROR={exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
