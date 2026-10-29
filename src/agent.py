from __future__ import annotations

import copy
import json
from typing import Any

from src.mock_model import ScriptedMockModel
from src.models import DecisionModel, ModelDecision
from src.openai_model import ModelAPIError
from src.tools import BusinessTools


SYSTEM_PROMPT = """你是素材审核与发布排期 Agent。你需要根据每轮工具观察自主选择下一动作，每轮必须且只能调用一个工具，不要用普通文本直接结束。
可用执行骨架：读取素材、检查规则、查询时段、按需保存草稿、finish 结束；你可依据观察跳过、补充或停止后续动作。
业务约束：
1. 缺必填信息或命中阻断规则时不得保存草稿。
2. 原要求时段可用时保持原时间；占用时可从 alternatives 选择建议时段。原 requested_start 是硬性要求，建议不同时间只能形成待确认草稿，不能声称用户已同意。
3. 只能保存草稿，不能发布。保存失败后不得声称成功，也不要自动重试。
4. save_draft 的 request_id、material_id、requested_start 必须与用户输入一致。
5. finish 的 summary 只写简短结论。最终任务和保存状态会由系统根据工具结果生成。
"""


def validate_scenario(scenario: dict[str, Any]) -> None:
    required = [
        "scenario_id",
        "request_id",
        "material_id",
        "requested_start",
        "max_steps",
        "max_tool_calls",
    ]
    missing = [key for key in required if key not in scenario]
    if missing:
        raise ValueError(f"场景缺少字段：{', '.join(missing)}")
    for key in ("scenario_id", "request_id", "material_id", "requested_start"):
        if not isinstance(scenario[key], str) or not scenario[key].strip():
            raise ValueError(f"{key} 必须是非空字符串")
    for key in ("max_steps", "max_tool_calls"):
        if type(scenario[key]) is not int or scenario[key] < 1:
            raise ValueError(f"{key} 必须是正整数")


def _summarize(name: str, result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return str(result.get("message", result.get("error_code", "工具执行失败")))
    if name == "get_material":
        return f"已读取素材 {result['material']['material_id']}。"
    if name == "check_rules":
        return (
            f"审核状态 {result['review_status']}；"
            f"缺失字段 {result['missing_fields']}；风险数 {len(result['risks'])}。"
        )
    if name == "query_slots":
        if result["requested_available"]:
            return "要求时段可用。"
        return f"要求时段占用；可用替代时段 {result['alternatives']}。"
    if name == "save_draft":
        return f"{result['message']} 草稿 ID：{result['draft']['draft_id']}。"
    if name == "finish":
        return "模型选择结束任务。"
    return "工具执行成功。"


class SchedulingAgent:
    def __init__(
        self,
        scenario: dict[str, Any],
        tools: BusinessTools,
        model: DecisionModel | None = None,
        mode: str = "mock",
    ):
        validate_scenario(scenario)
        if mode not in {"mock", "model"}:
            raise ValueError("mode 必须是 mock 或 model")
        self.scenario = copy.deepcopy(scenario)
        self.tools = tools
        self.model = model or ScriptedMockModel()
        self.mode = mode

    def _messages(self) -> list[dict[str, Any]]:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(self.scenario, ensure_ascii=False),
            },
        ]

    def _execute(self, name: str, arguments: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
        if name not in {"get_material", "check_rules", "query_slots", "save_draft"}:
            return {
                "ok": False,
                "error_code": "unknown_tool",
                "message": f"不支持的工具：{name}",
            }
        if name == "save_draft":
            if state.get("review", {}).get("review_status") != "passed":
                return {
                    "ok": False,
                    "save_status": "failed",
                    "error_code": "review_not_passed",
                    "message": "审核未通过，禁止保存草稿。",
                }
            if not state.get("slots", {}).get("ok"):
                return {
                    "ok": False,
                    "save_status": "failed",
                    "error_code": "slots_not_checked",
                    "message": "尚未成功查询时段，禁止保存草稿。",
                }
            slots = state["slots"]
            if slots.get("requested_start") != self.scenario["requested_start"]:
                return {
                    "ok": False,
                    "save_status": "failed",
                    "error_code": "slot_request_mismatch",
                    "message": "时段查询与用户原始请求不一致，已拒绝。",
                }
            if (
                slots.get("requested_available")
                and arguments.get("suggested_start") != self.scenario["requested_start"]
            ):
                return {
                    "ok": False,
                    "save_status": "failed",
                    "error_code": "original_slot_available",
                    "message": "原要求时段可用，禁止无故改用其他建议时段。",
                }
            expected = {
                "request_id": self.scenario["request_id"],
                "material_id": self.scenario["material_id"],
                "requested_start": self.scenario["requested_start"],
            }
            if any(arguments.get(key) != value for key, value in expected.items()):
                return {
                    "ok": False,
                    "save_status": "failed",
                    "error_code": "request_mismatch",
                    "message": "保存参数与用户原始请求不一致，已拒绝。",
                }
            if state.get("review_material_id") != self.scenario["material_id"]:
                return {
                    "ok": False,
                    "save_status": "failed",
                    "error_code": "review_mismatch",
                    "message": "审核对象与保存对象不一致，已拒绝。",
                }
        method = getattr(self.tools, name)
        try:
            return method(**arguments)
        except TypeError as error:
            return {
                "ok": False,
                "error_code": "invalid_tool_arguments",
                "message": f"工具参数无效：{error}",
            }

    def run(self) -> tuple[dict[str, Any], dict[str, Any]]:
        messages = self._messages()
        state: dict[str, Any] = {}
        steps: list[dict[str, Any]] = []
        tool_calls = 0
        termination: dict[str, Any] | None = None

        while len(steps) < self.scenario["max_steps"]:
            if tool_calls >= self.scenario["max_tool_calls"]:
                termination = {
                    "reason": "tool_call_limit",
                    "message": "已达到工具调用次数上限，任务未完成。",
                }
                break
            try:
                decision = self.model.next_decision(messages)
            except ModelAPIError as error:
                termination = {"reason": "model_error", "message": str(error)}
                break

            if decision.assistant_message:
                messages.append(decision.assistant_message)
            else:
                messages.append(
                    {"role": "assistant", "content": decision.rationale}
                )

            if decision.name == "finish":
                tool_calls += 1
                if (
                    set(decision.arguments) != {"summary"}
                    or not isinstance(decision.arguments["summary"], str)
                ):
                    result = {
                        "ok": False,
                        "error_code": "invalid_tool_arguments",
                        "message": "finish 必须且只能包含字符串 summary。",
                    }
                    steps.append(self._step_record(decision, result, len(steps) + 1))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": decision.tool_call_id,
                            "name": decision.name,
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
                    continue
                state["finish_summary"] = decision.arguments["summary"]
                result = {"ok": True, "status": "finished"}
                steps.append(self._step_record(decision, result, len(steps) + 1))
                termination = {"reason": "finished", "message": "模型选择结束。"}
                break

            tool_calls += 1
            result = self._execute(decision.name, decision.arguments, state)
            if (
                decision.name == "get_material"
                and result.get("ok")
                and decision.arguments.get("material_id") == self.scenario["material_id"]
            ):
                state["material"] = result["material"]
            elif (
                decision.name == "check_rules"
                and result.get("ok")
                and decision.arguments.get("material_id") == self.scenario["material_id"]
            ):
                state["review"] = result
                state["review_material_id"] = decision.arguments["material_id"]
            elif (
                decision.name == "query_slots"
                and result.get("ok")
                and decision.arguments.get("requested_start") == self.scenario["requested_start"]
            ):
                state["slots"] = result
            elif decision.name == "save_draft":
                if result.get("ok") or not state.get("save", {}).get("ok"):
                    state["save"] = result

            steps.append(self._step_record(decision, result, len(steps) + 1))
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": decision.tool_call_id,
                    "name": decision.name,
                    "content": json.dumps(result, ensure_ascii=False),
                }
            )

        if termination is None:
            termination = {
                "reason": "model_step_limit",
                "message": "已达到模型步数上限，任务未完成。",
            }

        for index, step in enumerate(steps):
            if index + 1 < len(steps):
                step["next_action"] = steps[index + 1]["action"]
            elif termination["reason"] == "finished":
                step["next_action"] = "end"
            else:
                step["next_action"] = f"stop:{termination['reason']}"

        output = self._build_output(state, termination, len(steps), tool_calls)
        trace = {
            "schema_version": "1.0",
            "scenario_id": self.scenario["scenario_id"],
            "request_id": self.scenario["request_id"],
            "agent_mode": self.mode,
            "real_model_decisions": self.mode == "model",
            "mock_boundary": (
                "脚本化流程桩，仅验证工具和状态链路，不证明真实模型决策效果。"
                if self.mode == "mock"
                else None
            ),
            "limits": {
                "max_steps": self.scenario["max_steps"],
                "max_tool_calls": self.scenario["max_tool_calls"],
            },
            "steps": steps,
            "termination": termination,
            "task_status": output["task_status"],
        }
        return output, trace

    @staticmethod
    def _step_record(
        decision: ModelDecision, result: dict[str, Any], number: int
    ) -> dict[str, Any]:
        return {
            "step": number,
            "decision_basis": decision.rationale[:500],
            "action": decision.name,
            "tool_arguments": decision.arguments,
            "observation_summary": _summarize(decision.name, result),
            "tool_result": result,
        }

    def _build_output(
        self,
        state: dict[str, Any],
        termination: dict[str, Any],
        model_steps: int,
        tool_calls: int,
    ) -> dict[str, Any]:
        review = state.get("review")
        slots = state.get("slots")
        save = state.get("save")
        requested = self.scenario["requested_start"]
        review_status = review.get("review_status", "not_checked") if review else "not_checked"
        risks = copy.deepcopy(review.get("risks", [])) if review else []
        if review and review.get("missing_fields"):
            risks.insert(
                0,
                {
                    "rule_id": "MISSING_FIELDS",
                    "message": f"缺少必填字段：{', '.join(review['missing_fields'])}",
                    "severity": "incomplete",
                },
            )

        suggested = None
        if save and save.get("draft"):
            suggested = save["draft"]["suggested_start"]
        elif slots:
            if slots.get("requested_available"):
                suggested = requested
            elif slots.get("alternatives"):
                suggested = slots["alternatives"][0]

        pending = []
        if suggested and suggested != requested and review_status == "passed":
            pending.append(f"请确认是否同意将建议时段设为 {suggested}；原硬性要求仍为 {requested}。")

        if save:
            draft = {
                "save_status": save.get("save_status", "failed"),
                "draft_id": save.get("draft", {}).get("draft_id"),
                "message": save.get("message", "草稿保存失败。"),
            }
        else:
            draft = {
                "save_status": "not_attempted",
                "draft_id": None,
                "message": "未尝试保存草稿。",
            }

        reason = termination["reason"]
        if reason in {"model_step_limit", "tool_call_limit"}:
            task_status = "incomplete"
            message = termination["message"]
        elif reason == "model_error":
            task_status = "failed"
            message = f"模型调用失败：{termination['message']}"
        elif review_status == "incomplete":
            task_status = "blocked"
            message = "素材信息不足，请补充缺失字段后重试。"
        elif review_status == "blocked":
            task_status = "blocked"
            message = "素材命中阻断规则，未保存排期草稿。"
        elif save and not save.get("ok"):
            task_status = "failed"
            message = f"草稿保存失败：{save.get('message', '未知错误')}"
        elif save and save.get("ok"):
            if pending:
                task_status = "waiting_confirmation"
                message = "排期草稿已保存，替代时段待用户确认；未执行发布。"
            else:
                task_status = "completed"
                message = "排期草稿已保存；未执行发布。"
        elif slots and not slots.get("requested_available") and not slots.get("alternatives"):
            task_status = "incomplete"
            message = "要求时段占用且没有可用替代时段，任务未完成。"
        else:
            task_status = "incomplete"
            message = "工具流程尚未完成，未形成已保存的排期草稿。"

        return {
            "schema_version": "1.0",
            "scenario_id": self.scenario["scenario_id"],
            "request_id": self.scenario["request_id"],
            "material_id": self.scenario["material_id"],
            "agent_mode": self.mode,
            "real_model_decisions": self.mode == "model",
            "task_status": task_status,
            "review_status": review_status,
            "risk_reasons": risks,
            "requested_start": requested,
            "suggested_start": suggested,
            "pending_confirmations": pending,
            "draft": draft,
            "publication_status": "draft_only" if save and save.get("ok") else "not_saved",
            "message": message,
            "usage": {"model_steps": model_steps, "tool_calls": tool_calls},
            "termination_reason": reason,
        }
