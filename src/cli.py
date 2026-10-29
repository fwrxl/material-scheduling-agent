from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from src.agent import SchedulingAgent
from src.mock_model import ScriptedMockModel
from src.openai_model import ModelAPIError, OpenAICompatibleModel
from src.tools import BusinessTools


SCENARIOS = [
    "normal",
    "missing_info",
    "rule_risk",
    "slot_conflict",
    "duplicate",
    "save_failure",
    "step_limit",
    "tool_call_limit",
]


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: str | Path, data: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def make_model(mode: str):
    if mode == "model":
        return OpenAICompatibleModel.from_env()
    return ScriptedMockModel()


def execute(
    scenario: dict[str, Any],
    mode: str,
    data_dir: str | Path,
    store_path: str | Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    tools = BusinessTools(
        data_dir=data_dir,
        store_path=store_path,
        failures=scenario.get("failures"),
    )
    agent = SchedulingAgent(
        scenario=scenario,
        tools=tools,
        model=make_model(mode),
        mode=mode,
    )
    return agent.run()


def run_one(args: argparse.Namespace) -> int:
    scenario = load_json(args.input)
    output, trace = execute(scenario, args.mode, args.data_dir, args.store_path)
    if args.output_dir:
        destination = Path(args.output_dir)
        write_json(destination / "output.json", output)
        write_json(destination / "trace.json", trace)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def run_all(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    inputs_dir = Path(args.inputs_dir) if args.inputs_dir else output_dir / "inputs"
    store_path = Path(args.store_path) if args.store_path else output_dir / "drafts.json"
    if args.reset_store and store_path.exists():
        store_path.unlink()
    if store_path.exists() and not args.reset_store:
        raise ValueError("批量运行需要新草稿存储；请使用 --reset-store 或更换 --store-path。")

    manifest: dict[str, Any] = {
        "agent_mode": args.mode,
        "real_model_decisions": args.mode == "model",
        "mock_boundary": (
            "以下均为脚本化 Mock 的实际运行结果，只验证流程，不证明真实模型决策效果。"
            if args.mode == "mock"
            else None
        ),
        "runs": [],
    }
    for scenario_id in SCENARIOS:
        scenario = load_json(inputs_dir / f"{scenario_id}.json")
        run_names = (
            ["duplicate_first", "duplicate_second"]
            if scenario_id == "duplicate"
            else [scenario_id]
        )
        for run_name in run_names:
            output, trace = execute(scenario, args.mode, args.data_dir, store_path)
            write_json(output_dir / "outputs" / f"{run_name}.json", output)
            write_json(output_dir / "traces" / f"{run_name}.json", trace)
            manifest["runs"].append(
                {
                    "run": run_name,
                    "input": f"inputs/{scenario_id}.json",
                    "output": f"outputs/{run_name}.json",
                    "trace": f"traces/{run_name}.json",
                    "task_status": output["task_status"],
                    "save_status": output["draft"]["save_status"],
                }
            )
            print(f"{run_name}: {output['task_status']} / {output['draft']['save_status']}")
    write_json(output_dir / "manifest.json", manifest)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="素材审核与发布排期 Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    one = subparsers.add_parser("run", help="运行一个 JSON 场景")
    one.add_argument("--mode", choices=["mock", "model"], default="mock")
    one.add_argument("--input", required=True, help="场景 JSON 路径")
    one.add_argument("--data-dir", default="data")
    one.add_argument("--store-path", default=".runtime/drafts.json")
    one.add_argument("--output-dir", help="可选：写入 output.json 和 trace.json")
    one.set_defaults(handler=run_one)

    batch = subparsers.add_parser("run-all", help="运行全部验收场景并生成 examples 材料")
    batch.add_argument("--mode", choices=["mock", "model"], default="mock")
    batch.add_argument("--data-dir", default="data")
    batch.add_argument("--inputs-dir")
    batch.add_argument("--output-dir", default="examples")
    batch.add_argument("--store-path")
    batch.add_argument("--reset-store", action="store_true")
    batch.set_defaults(handler=run_all)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.handler(args)
    except (OSError, ValueError, json.JSONDecodeError, ModelAPIError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
