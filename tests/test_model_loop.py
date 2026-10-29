from __future__ import annotations

import json

from src.agent import SchedulingAgent
from src.openai_model import OpenAICompatibleModel
from src.tools import BusinessTools


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def response(call_id, name, arguments):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": f"选择 {name}",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                }
            }
        ]
    }


def test_model_mode_executes_multi_turn_observation_loop(
    monkeypatch, load_scenario, data_dir, tmp_path
):
    scenario = load_scenario("slot_conflict")
    replies = [
        response("call-1", "get_material", {"material_id": "M04"}),
        response("call-2", "check_rules", {"material_id": "M04"}),
        response(
            "call-3",
            "query_slots",
            {"requested_start": scenario["requested_start"]},
        ),
        response(
            "call-4",
            "save_draft",
            {
                "request_id": scenario["request_id"],
                "material_id": "M04",
                "requested_start": scenario["requested_start"],
                "suggested_start": "2027-04-10T19:00:00+08:00",
            },
        ),
        response("call-5", "finish", {"summary": "结束"}),
    ]
    requests = []

    def fake_urlopen(request, timeout):
        body = json.loads(request.data)
        requests.append(body)
        return FakeResponse(replies[len(requests) - 1])

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    model = OpenAICompatibleModel("https://model.example/v1", "test-model")
    tools = BusinessTools(data_dir, tmp_path / "drafts.json")
    output, trace = SchedulingAgent(
        scenario, tools, model=model, mode="model"
    ).run()

    assert output["task_status"] == "waiting_confirmation"
    assert output["real_model_decisions"] is True
    assert len(requests) == 5
    save_request_observations = [
        message for message in requests[3]["messages"] if message["role"] == "tool"
    ]
    slot_observation = json.loads(save_request_observations[-1]["content"])
    assert slot_observation["requested_available"] is False
    assert trace["steps"][2]["next_action"] == "save_draft"
