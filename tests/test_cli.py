from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_cli_run_writes_output_and_trace(tmp_path):
    output_dir = tmp_path / "run"
    command = [
        sys.executable,
        "-m",
        "src.cli",
        "run",
        "--mode",
        "mock",
        "--input",
        "examples/inputs/normal.json",
        "--store-path",
        str(tmp_path / "drafts.json"),
        "--output-dir",
        str(output_dir),
    ]
    completed = subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True)
    printed = json.loads(completed.stdout)
    saved = json.loads((output_dir / "output.json").read_text(encoding="utf-8"))
    trace = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
    assert printed == saved
    assert saved["task_status"] == "completed"
    assert trace["steps"]


def test_cli_rejects_invalid_scenario(tmp_path):
    scenario = tmp_path / "invalid.json"
    scenario.write_text("{}", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.cli",
            "run",
            "--input",
            str(scenario),
            "--store-path",
            str(tmp_path / "drafts.json"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 2
    assert "场景缺少字段" in completed.stderr
