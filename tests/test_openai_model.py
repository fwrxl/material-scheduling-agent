from __future__ import annotations

import io
import json
import urllib.error

import pytest

from src.openai_model import ModelAPIError, OpenAICompatibleModel


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def tool_response(name="get_material", arguments=None):
    return {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "先读取素材。",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments or {"material_id": "M01"}),
                            },
                        }
                    ],
                }
            }
        ]
    }


def test_openai_compatible_tool_call_is_parsed(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data)
        captured["authorization"] = request.get_header("Authorization")
        return FakeResponse(tool_response())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    model = OpenAICompatibleModel("https://model.example/v1", "test-model", "secret", 12)
    decision = model.next_decision([{"role": "user", "content": "test"}])
    assert decision.name == "get_material"
    assert decision.arguments == {"material_id": "M01"}
    assert decision.rationale == "先读取素材。"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["tools"]
    assert captured["authorization"] == "Bearer secret"


def test_plain_text_is_not_accepted_as_tool_decision(monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: FakeResponse(
            {"choices": [{"message": {"role": "assistant", "content": "已完成"}}]}
        ),
    )
    model = OpenAICompatibleModel("https://model.example/v1", "test-model")
    with pytest.raises(ModelAPIError, match="tool_calls"):
        model.next_decision([])


def test_http_error_is_exposed_without_retry(monkeypatch):
    def fail(request, timeout):
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "rate limited",
            hdrs=None,
            fp=io.BytesIO(b"quota exceeded"),
        )

    monkeypatch.setattr("urllib.request.urlopen", fail)
    model = OpenAICompatibleModel("https://model.example/v1", "test-model")
    with pytest.raises(ModelAPIError, match="HTTP 429"):
        model.next_decision([])


def test_environment_configuration_is_required(monkeypatch):
    monkeypatch.delenv("AGENT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("AGENT_MODEL_NAME", raising=False)
    with pytest.raises(ModelAPIError, match="AGENT_MODEL_BASE_URL"):
        OpenAICompatibleModel.from_env()
