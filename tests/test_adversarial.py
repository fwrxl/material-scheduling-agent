from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.agent import SchedulingAgent
from src.mock_model import ScriptedMockModel
from src.models import ModelDecision
from src.openai_model import ModelAPIError, OpenAICompatibleModel
from src.tools import BusinessTools


ROOT = Path(__file__).resolve().parents[1]


class QueueModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def next_decision(self, messages):
        name, arguments = next(self.decisions)
        return ModelDecision(name, arguments, "对抗测试动作", "test-call")


def save_arguments(scenario, suggested_start=None):
    return {
        "request_id": scenario["request_id"],
        "material_id": scenario["material_id"],
        "requested_start": scenario["requested_start"],
        "suggested_start": suggested_start or scenario["requested_start"],
    }


def test_later_failed_save_does_not_erase_successful_draft(
    load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("slot_conflict")
    decisions = [
        ("get_material", {"material_id": "M04"}),
        ("check_rules", {"material_id": "M04"}),
        ("query_slots", {"requested_start": scenario["requested_start"]}),
        ("save_draft", save_arguments(scenario, "2027-04-10T19:00:00+08:00")),
        ("save_draft", save_arguments(scenario, "2027-04-10T20:00:00+08:00")),
        ("finish", {"summary": "结束"}),
    ]
    store = tmp_path / "drafts.json"
    tools = BusinessTools(data_dir, store)
    output, trace = SchedulingAgent(scenario, tools, QueueModel(decisions)).run()
    assert trace["steps"][3]["tool_result"]["save_status"] == "saved"
    assert trace["steps"][4]["tool_result"]["error_code"] == "idempotency_conflict"
    assert output["task_status"] == "waiting_confirmation"
    assert output["draft"]["save_status"] == "saved"
    assert output["draft"]["draft_id"] == trace["steps"][3]["tool_result"]["draft"]["draft_id"]
    assert output["publication_status"] == "draft_only"
    assert len(json.loads(store.read_text(encoding="utf-8"))) == 1


def test_other_material_review_does_not_pollute_current_result(
    load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("normal")
    decisions = [
        ("get_material", {"material_id": "M01"}),
        ("check_rules", {"material_id": "M01"}),
        ("query_slots", {"requested_start": scenario["requested_start"]}),
        ("save_draft", save_arguments(scenario)),
        ("check_rules", {"material_id": "M03"}),
        ("finish", {"summary": "结束"}),
    ]
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    output, trace = SchedulingAgent(scenario, tools, QueueModel(decisions)).run()
    assert trace["steps"][4]["tool_result"]["review_status"] == "blocked"
    assert output["task_status"] == "completed"
    assert output["review_status"] == "passed"
    assert output["risk_reasons"] == []
    assert output["draft"]["save_status"] == "saved"


@pytest.mark.parametrize("failed_tool", ["get_material", "check_rules", "query_slots"])
def test_mock_stops_after_tool_failure_without_saving(
    failed_tool, load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("normal")
    scenario["failures"] = {failed_tool: "注入失败"}
    store = tmp_path / "drafts.json"
    tools = BusinessTools(data_dir, store, failures=scenario["failures"])
    output, trace = SchedulingAgent(scenario, tools, ScriptedMockModel()).run()
    actions = [step["action"] for step in trace["steps"]]
    assert "save_draft" not in actions
    assert actions[-1] == "finish"
    assert output["draft"]["save_status"] == "not_attempted"
    assert output["task_status"] == "incomplete"
    assert not store.exists()


def test_invalid_finish_is_failed_observation_not_success(
    load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("normal")
    decisions = [
        ("finish", {}),
        ("finish", {"summary": "收到格式错误后修正"}),
    ]
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    output, trace = SchedulingAgent(scenario, tools, QueueModel(decisions)).run()
    assert trace["steps"][0]["tool_result"]["ok"] is False
    assert trace["steps"][0]["tool_result"]["error_code"] == "invalid_tool_arguments"
    assert trace["steps"][0]["next_action"] == "finish"
    assert trace["steps"][1]["tool_result"]["ok"] is True
    assert output["termination_reason"] == "finished"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_id", 123),
        ("material_id", ""),
        ("max_steps", True),
        ("max_tool_calls", True),
    ],
)
def test_invalid_scenario_types_are_rejected(
    field, value, load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("normal")
    scenario[field] = value
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    with pytest.raises(ValueError):
        SchedulingAgent(scenario, tools, ScriptedMockModel())


@pytest.mark.parametrize("content", ['[{}]', '["bad"]'])
def test_corrupt_store_records_return_failure(
    content, load_scenario, data_dir, tmp_path
):
    store = tmp_path / "drafts.json"
    store.write_text(content, encoding="utf-8")
    tools = BusinessTools(data_dir, store)
    result = tools.save_draft(**save_arguments(load_scenario("normal")))
    assert result["ok"] is False
    assert result["save_status"] == "failed"
    assert result["error_code"] == "storage_error"


class FakeResponse:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": self.message}]}).encode("utf-8")


def valid_message():
    return {
        "role": "assistant",
        "content": "读取素材",
        "tool_calls": [
            {
                "id": "call-1",
                "type": "function",
                "function": {
                    "name": "get_material",
                    "arguments": '{"material_id":"M01"}',
                },
            }
        ],
    }


def malformed_messages():
    null_calls = valid_message()
    null_calls["tool_calls"] = None
    object_content = valid_message()
    object_content["content"] = {"text": "bad"}
    null_id = valid_message()
    null_id["tool_calls"][0]["id"] = None
    invalid_name = valid_message()
    invalid_name["tool_calls"][0]["function"]["name"] = []
    null_function = valid_message()
    null_function["tool_calls"][0]["function"] = None
    null_arguments = valid_message()
    null_arguments["tool_calls"][0]["function"]["arguments"] = None
    return [
        null_calls,
        object_content,
        null_id,
        invalid_name,
        null_function,
        null_arguments,
    ]


@pytest.mark.parametrize("message", malformed_messages())
def test_malformed_model_responses_raise_controlled_error(message, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(message),
    )
    model = OpenAICompatibleModel("https://model.example/v1", "test-model")
    with pytest.raises(ModelAPIError):
        model.next_decision([])


def test_agent_converts_malformed_response_to_failed_result(
    load_scenario, data_dir, tmp_path, monkeypatch
):
    message = valid_message()
    message["tool_calls"] = None
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(message),
    )
    model = OpenAICompatibleModel("https://model.example/v1", "test-model")
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    output, trace = SchedulingAgent(
        load_scenario("normal"), tools, model=model, mode="model"
    ).run()
    assert output["task_status"] == "failed"
    assert output["termination_reason"] == "model_error"
    assert output["draft"]["save_status"] == "not_attempted"
    assert trace["termination"]["reason"] == "model_error"


def test_repeated_cli_processes_keep_one_draft(tmp_path):
    store = tmp_path / "drafts.json"
    outputs = []
    for index in range(2):
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.cli",
                "run",
                "--mode",
                "mock",
                "--input",
                "examples/inputs/duplicate.json",
                "--store-path",
                str(store),
                "--output-dir",
                str(tmp_path / f"run-{index}"),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        outputs.append(json.loads(completed.stdout))
    assert outputs[0]["draft"]["save_status"] == "saved"
    assert outputs[1]["draft"]["save_status"] == "duplicate"
    assert outputs[0]["draft"]["draft_id"] == outputs[1]["draft"]["draft_id"]
    assert len(json.loads(store.read_text(encoding="utf-8"))) == 1
