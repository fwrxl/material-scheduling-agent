from __future__ import annotations

import json

import pytest

from src.agent import SchedulingAgent
from src.mock_model import ScriptedMockModel
from src.models import ModelDecision
from src.tools import BusinessTools


def run_scenario(scenario, data_dir, store_path):
    tools = BusinessTools(data_dir, store_path, failures=scenario.get("failures"))
    return SchedulingAgent(scenario, tools, ScriptedMockModel(), mode="mock").run()


@pytest.mark.parametrize(
    ("name", "task_status", "review_status", "save_status"),
    [
        ("normal", "completed", "passed", "saved"),
        ("missing_info", "blocked", "incomplete", "not_attempted"),
        ("rule_risk", "blocked", "blocked", "not_attempted"),
        ("slot_conflict", "waiting_confirmation", "passed", "saved"),
        ("save_failure", "failed", "passed", "failed"),
        ("step_limit", "incomplete", "not_checked", "not_attempted"),
        ("tool_call_limit", "incomplete", "passed", "not_attempted"),
    ],
)
def test_acceptance_paths(
    name,
    task_status,
    review_status,
    save_status,
    load_scenario,
    data_dir,
    tmp_path,
):
    output, _ = run_scenario(load_scenario(name), data_dir, tmp_path / "drafts.json")
    assert output["task_status"] == task_status
    assert output["review_status"] == review_status
    assert output["draft"]["save_status"] == save_status
    assert output["real_model_decisions"] is False


def test_conflict_preserves_hard_requirement_and_requests_confirmation(
    load_scenario, data_dir, tmp_path
):
    output, trace = run_scenario(
        load_scenario("slot_conflict"), data_dir, tmp_path / "drafts.json"
    )
    assert output["requested_start"] == "2027-04-10T18:00:00+08:00"
    assert output["suggested_start"] == "2027-04-10T19:00:00+08:00"
    assert output["pending_confirmations"]
    assert output["publication_status"] == "draft_only"
    assert [step["action"] for step in trace["steps"]] == [
        "get_material",
        "check_rules",
        "query_slots",
        "save_draft",
        "finish",
    ]
    assert trace["steps"][2]["next_action"] == "save_draft"


def test_normal_and_conflict_have_different_observation_driven_paths(
    load_scenario, data_dir, tmp_path
):
    normal, normal_trace = run_scenario(
        load_scenario("normal"), data_dir, tmp_path / "normal.json"
    )
    conflict, conflict_trace = run_scenario(
        load_scenario("slot_conflict"), data_dir, tmp_path / "conflict.json"
    )
    normal_save = normal_trace["steps"][3]["tool_arguments"]
    conflict_save = conflict_trace["steps"][3]["tool_arguments"]
    assert normal_save["suggested_start"] == normal_save["requested_start"]
    assert conflict_save["suggested_start"] != conflict_save["requested_start"]
    assert normal["task_status"] != conflict["task_status"]


def test_repeated_agent_execution_creates_only_one_draft(
    load_scenario, data_dir, tmp_path
):
    store = tmp_path / "drafts.json"
    scenario = load_scenario("duplicate")
    first, _ = run_scenario(scenario, data_dir, store)
    second, _ = run_scenario(scenario, data_dir, store)
    drafts = json.loads(store.read_text(encoding="utf-8"))
    assert first["draft"]["save_status"] == "saved"
    assert second["draft"]["save_status"] == "duplicate"
    assert second["draft"]["draft_id"] == first["draft"]["draft_id"]
    assert len(drafts) == 1


def test_save_failure_is_not_converted_to_success(load_scenario, data_dir, tmp_path):
    output, trace = run_scenario(
        load_scenario("save_failure"), data_dir, tmp_path / "drafts.json"
    )
    assert output["task_status"] == "failed"
    assert output["draft"] == {
        "save_status": "failed",
        "draft_id": None,
        "message": "模拟存储服务不可用",
    }
    assert output["publication_status"] == "not_saved"
    assert not (tmp_path / "drafts.json").exists()
    assert trace["steps"][-2]["next_action"] == "finish"


def test_step_limit_has_explicit_incomplete_trace(load_scenario, data_dir, tmp_path):
    output, trace = run_scenario(
        load_scenario("step_limit"), data_dir, tmp_path / "drafts.json"
    )
    assert output["termination_reason"] == "model_step_limit"
    assert output["task_status"] == "incomplete"
    assert trace["steps"][-1]["next_action"] == "stop:model_step_limit"


def test_tool_call_limit_has_explicit_incomplete_trace(load_scenario, data_dir, tmp_path):
    scenario = load_scenario("normal")
    scenario["max_tool_calls"] = 1
    output, trace = run_scenario(scenario, data_dir, tmp_path / "drafts.json")
    assert output["termination_reason"] == "tool_call_limit"
    assert output["task_status"] == "incomplete"
    assert trace["steps"][-1]["next_action"] == "stop:tool_call_limit"


class QueueModel:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def next_decision(self, messages):
        name, arguments = next(self.decisions)
        return ModelDecision(name, arguments, "测试动作", "test-call")


def test_agent_rejects_model_attempt_to_change_original_request(
    load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("normal")
    decisions = [
        ("get_material", {"material_id": "M01"}),
        ("check_rules", {"material_id": "M01"}),
        ("query_slots", {"requested_start": scenario["requested_start"]}),
        (
            "save_draft",
            {
                "request_id": scenario["request_id"],
                "material_id": "M01",
                "requested_start": "2027-04-10T18:00:00+08:00",
                "suggested_start": "2027-04-10T19:00:00+08:00",
            },
        ),
        ("finish", {"summary": "试图篡改原始时间"}),
    ]
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    output, _ = SchedulingAgent(scenario, tools, QueueModel(decisions)).run()
    assert output["task_status"] == "failed"
    assert output["draft"]["save_status"] == "failed"
    assert output["requested_start"] == scenario["requested_start"]
    assert not tools.store.path.exists()


def test_agent_rejects_save_before_review(load_scenario, data_dir, tmp_path):
    scenario = load_scenario("normal")
    decisions = [
        (
            "save_draft",
            {
                "request_id": scenario["request_id"],
                "material_id": "M01",
                "requested_start": scenario["requested_start"],
                "suggested_start": scenario["requested_start"],
            },
        ),
        ("finish", {"summary": "结束"}),
    ]
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    output, _ = SchedulingAgent(scenario, tools, QueueModel(decisions)).run()
    assert output["task_status"] == "failed"
    assert output["draft"]["save_status"] == "failed"
    assert not tools.store.path.exists()


def test_agent_rejects_alternative_when_original_slot_is_available(
    load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("normal")
    decisions = [
        ("get_material", {"material_id": "M01"}),
        ("check_rules", {"material_id": "M01"}),
        ("query_slots", {"requested_start": scenario["requested_start"]}),
        (
            "save_draft",
            {
                "request_id": scenario["request_id"],
                "material_id": "M01",
                "requested_start": scenario["requested_start"],
                "suggested_start": "2027-04-10T20:00:00+08:00",
            },
        ),
        ("finish", {"summary": "结束"}),
    ]
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    output, trace = SchedulingAgent(scenario, tools, QueueModel(decisions)).run()
    assert output["task_status"] == "failed"
    assert trace["steps"][-2]["tool_result"]["error_code"] == "original_slot_available"
    assert not tools.store.path.exists()
