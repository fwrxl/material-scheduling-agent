from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ModelDecision:
    name: str
    arguments: dict[str, Any]
    rationale: str
    tool_call_id: str
    assistant_message: dict[str, Any] | None = None


class DecisionModel(Protocol):
    def next_decision(self, messages: list[dict[str, Any]]) -> ModelDecision:
        """根据当前对话和工具观察选择下一个动作。"""
