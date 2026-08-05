from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from collections.abc import Sequence
from pathlib import Path

from cashflow_direct.intake import choose_input_files
from cashflow_direct.pipeline import (
    confirm_mapping,
    confirm_cash_scope,
    finalize_run,
    import_ai_results,
    run_classification,
    run_preflight,
    supplement_cash_balances,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="一般企业直接法现金流量表编制与校验")
    commands = parser.add_subparsers(dest="command")
    preflight = commands.add_parser("preflight", help="选择并登记资料")
    preflight.add_argument("--overall", required=True, help="财务报表整体重要性，单位元")
    preflight.add_argument("--performance", required=True, help="实际执行的重要性，单位元")
    preflight.add_argument("--trivial", required=True, help="明显微小错报临界值，单位元")
    preflight.add_argument("--output-parent", help="由 Skill 自动传递的输出父目录")
    for name in (
        "confirm-mapping",
        "confirm-cash",
        "supplement-cash",
        "classify",
        "import-ai",
        "finalize",
        "status",
    ):
        command = commands.add_parser(name)
        command.add_argument("--run-dir", required=True, help="由上一阶段自动传递的运行目录")
        if name == "confirm-mapping":
            command.add_argument("--decisions", required=True, help="字段映射确认 JSON")
        elif name == "confirm-cash":
            command.add_argument("--decisions", required=True, help="现金范围决定 JSON")
        elif name == "supplement-cash":
            command.add_argument("--opening", required=True, help="期初现金余额，单位元")
            command.add_argument("--closing", required=True, help="期末现金余额，单位元")
            command.add_argument("--fx", required=True, help="汇率变动影响，单位元；没有则填零")
            command.add_argument("--source-note", required=True, help="补充数据的资料来源说明")
        elif name == "import-ai":
            command.add_argument("--result-path", required=True, help="AI 返回结果 JSONL")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if arguments.command is None:
        build_parser().print_help()
        return 0
    try:
        if arguments.command == "preflight":
            result = run_preflight(
                choose_input_files(),
                (arguments.overall, arguments.performance, arguments.trivial),
                None if arguments.output_parent is None else Path(arguments.output_parent),
            )
        elif arguments.command == "confirm-mapping":
            result = confirm_mapping(Path(arguments.run_dir), json.loads(arguments.decisions))
        elif arguments.command == "confirm-cash":
            result = confirm_cash_scope(Path(arguments.run_dir), json.loads(arguments.decisions))
        elif arguments.command == "supplement-cash":
            result = supplement_cash_balances(
                Path(arguments.run_dir),
                arguments.opening,
                arguments.closing,
                arguments.fx,
                arguments.source_note,
            )
        elif arguments.command == "classify":
            result = run_classification(Path(arguments.run_dir))
        elif arguments.command == "import-ai":
            result = import_ai_results(Path(arguments.run_dir), Path(arguments.result_path))
        elif arguments.command == "finalize":
            result = finalize_run(Path(arguments.run_dir))
        else:
            state_path = Path(arguments.run_dir) / "计算留痕数据" / "运行状态.json"
            result = json.loads(state_path.read_text(encoding="utf-8-sig"))
        payload = asdict(result) if hasattr(result, "__dataclass_fields__") else result
        print(json.dumps(payload, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, ensure_ascii=False))
        return 1
