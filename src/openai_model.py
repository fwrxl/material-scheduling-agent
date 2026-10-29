from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from src.models import ModelDecision
from src.tools import TOOL_SCHEMAS


class ModelAPIError(RuntimeError):
    pass


class OpenAICompatibleModel:
    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: str | None = None,
        timeout: float = 60,
    ):
        self.endpoint = f"{base_url.rstrip('/')}/chat/completions"
        self.model_name = model_name
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "OpenAICompatibleModel":
        base_url = os.getenv("AGENT_MODEL_BASE_URL")
        model_name = os.getenv("AGENT_MODEL_NAME")
        if not base_url or not model_name:
            raise ModelAPIError(
                "真实模型模式需要 AGENT_MODEL_BASE_URL 和 AGENT_MODEL_NAME。"
            )
        try:
            timeout = float(os.getenv("AGENT_MODEL_TIMEOUT", "60"))
        except ValueError as error:
            raise ModelAPIError("AGENT_MODEL_TIMEOUT 必须是数字。") from error
        return cls(
            base_url=base_url,
            model_name=model_name,
            api_key=os.getenv("AGENT_MODEL_API_KEY"),
            timeout=timeout,
        )

    def next_decision(self, messages: list[dict[str, Any]]) -> ModelDecision:
        body = {
            "model": self.model_name,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
            "temperature": 0,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read(1000).decode("utf-8", errors="replace")
            raise ModelAPIError(f"模型 API 返回 HTTP {error.code}：{detail}") from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ModelAPIError(f"模型 API 调用失败：{error}") from error

        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise ModelAPIError("模型响应缺少有效的 message。") from error
        if not isinstance(message, dict):
            raise ModelAPIError("模型响应 message 必须是对象。")
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            raise ModelAPIError("模型响应缺少 message.tool_calls；未将普通文本视为决策。")
        if len(tool_calls) != 1:
            raise ModelAPIError("每轮必须且只能调用一个工具。")
        call = tool_calls[0]
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise ModelAPIError("模型工具调用格式无效。")
        function = call["function"]
        call_id = call.get("id")
        name = function.get("name")
        raw_arguments = function.get("arguments")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ModelAPIError("工具调用 id 必须是非空字符串。")
        if not isinstance(name, str) or not name.strip():
            raise ModelAPIError("工具名称必须是非空字符串。")
        if not isinstance(raw_arguments, str):
            raise ModelAPIError("工具参数必须是 JSON 字符串。")
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError as error:
            raise ModelAPIError("工具参数不是有效 JSON。") from error
        if not isinstance(arguments, dict):
            raise ModelAPIError("工具参数必须是 JSON 对象。")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise ModelAPIError("模型响应 content 必须是字符串或 null。")
        rationale = (content or "模型根据当前观察选择该动作。").strip()
        return ModelDecision(
            name=name,
            arguments=arguments,
            rationale=rationale,
            tool_call_id=call_id,
            assistant_message=message,
        )
