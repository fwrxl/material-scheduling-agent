from __future__ import annotations

import json
from typing import Any

from src.models import ModelDecision


class ScriptedMockModel:
    """离线流程桩：只验证工具和状态链路，不代表真实模型决策能力。"""

    def __init__(self):
        self.counter = 0

    def _decision(self, name: str, arguments: dict[str, Any], rationale: str) -> ModelDecision:
        self.counter += 1
        return ModelDecision(
            name=name,
            arguments=arguments,
            rationale=rationale,
            tool_call_id=f"mock-call-{self.counter}",
        )

    def next_decision(self, messages: list[dict[str, Any]]) -> ModelDecision:
        scenario = json.loads(messages[1]["content"])
        observations: dict[str, dict[str, Any]] = {}
        for message in messages:
            if message.get("role") == "tool":
                observations[message["name"]] = json.loads(message["content"])

        material = observations.get("get_material")
        if material is None:
            return self._decision(
                "get_material",
                {"material_id": scenario["material_id"]},
                "需要先读取素材，确认后续审核对象。",
            )
        if not material.get("ok"):
            return self._decision("finish", {"summary": "素材读取失败，任务未完成。"}, "读取失败，停止后续排期。")

        review = observations.get("check_rules")
        if review is None:
            return self._decision(
                "check_rules",
                {"material_id": scenario["material_id"]},
                "素材已读取，继续检查必填字段和审核规则。",
            )
        if not review.get("ok"):
            return self._decision(
                "finish",
                {"summary": "审核工具失败，任务未完成。"},
                "无法获得审核结论，停止查询时段和保存。",
            )
        if review.get("review_status") == "incomplete":
            return self._decision(
                "finish",
                {"summary": "素材必填信息不足，请补充后再排期。"},
                "观察到缺失字段，不能保存草稿。",
            )
        if review.get("review_status") == "blocked":
            return self._decision(
                "finish",
                {"summary": "素材命中阻断规则，未进入排期。"},
                "观察到阻断风险，停止排期。",
            )

        slots = observations.get("query_slots")
        if slots is None:
            return self._decision(
                "query_slots",
                {"requested_start": scenario["requested_start"]},
                "审核通过，查询用户要求时段及替代时段。",
            )
        if not slots.get("ok"):
            return self._decision("finish", {"summary": "时段查询失败，任务未完成。"}, "无法确认时段可用性，停止保存。")

        saved = observations.get("save_draft")
        if saved is None:
            if slots["requested_available"]:
                suggested = scenario["requested_start"]
                rationale = "要求时段可用，保持用户的硬性发布时间。"
            elif slots.get("alternatives"):
                suggested = slots["alternatives"][0]
                rationale = "要求时段已占用，选择可用替代时段并保留待确认边界。"
            else:
                return self._decision(
                    "finish",
                    {"summary": "要求时段占用且无可用替代时段。"},
                    "没有可行时段，不能擅自更改硬性要求。",
                )
            return self._decision(
                "save_draft",
                {
                    "request_id": scenario["request_id"],
                    "material_id": scenario["material_id"],
                    "requested_start": scenario["requested_start"],
                    "suggested_start": suggested,
                },
                rationale,
            )
        if saved.get("ok"):
            if saved["draft"]["confirmation_status"] == "pending":
                summary = "草稿保存流程结束；替代时段仍需用户确认。"
            else:
                summary = "原要求时段的排期草稿已保存，无需额外确认。"
        else:
            summary = "草稿保存失败，任务未完成。"
        return self._decision("finish", {"summary": summary}, "已观察保存结果，如实结束任务。")
