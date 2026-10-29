from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_material",
            "description": "读取素材元数据。",
            "parameters": {
                "type": "object",
                "properties": {"material_id": {"type": "string"}},
                "required": ["material_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_rules",
            "description": "检查素材必填信息和审核规则，返回缺失字段与风险。",
            "parameters": {
                "type": "object",
                "properties": {"material_id": {"type": "string"}},
                "required": ["material_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_slots",
            "description": "查询要求时段是否可用，并返回同日可选替代时段。",
            "parameters": {
                "type": "object",
                "properties": {"requested_start": {"type": "string"}},
                "required": ["requested_start"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "save_draft",
            "description": "保存排期草稿，不执行发布。相同 request_id 重复保存返回原草稿。",
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "string"},
                    "material_id": {"type": "string"},
                    "requested_start": {"type": "string"},
                    "suggested_start": {"type": "string"},
                },
                "required": [
                    "request_id",
                    "material_id",
                    "requested_start",
                    "suggested_start",
                ],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "finish",
            "description": "结束当前任务并给出简短结论；最终状态由实际工具结果计算。",
            "parameters": {
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
        },
    },
]


class DraftStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError("草稿存储必须是 JSON 数组")
        required_fields = {
            "request_id",
            "material_id",
            "requested_start",
            "suggested_start",
            "review_status",
            "confirmation_status",
            "publish_status",
            "draft_id",
            "created_at",
        }
        for record in data:
            if not isinstance(record, dict) or not required_fields.issubset(record):
                raise ValueError("草稿存储包含结构不完整的记录")
            if not isinstance(record["request_id"], str) or not record["request_id"]:
                raise ValueError("草稿记录包含无效的 request_id")
            if not isinstance(record["draft_id"], str) or not record["draft_id"]:
                raise ValueError("草稿记录包含无效的 draft_id")
        return data

    def _write(self, drafts: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix="drafts-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = handle.name
                json.dump(drafts, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path and os.path.exists(temporary_path):
                os.unlink(temporary_path)

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            drafts = self._load()
            for existing in drafts:
                if existing["request_id"] != payload["request_id"]:
                    continue
                comparable = {key: existing.get(key) for key in payload}
                if comparable != payload:
                    return {
                        "ok": False,
                        "error_code": "idempotency_conflict",
                        "message": "相同 request_id 对应了不同的排期内容。",
                    }
                return {"ok": True, "status": "duplicate", "draft": existing}

            digest = hashlib.sha256(payload["request_id"].encode("utf-8")).hexdigest()[:12]
            draft = {
                **copy.deepcopy(payload),
                "draft_id": f"DRAFT-{digest}",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            drafts.append(draft)
            self._write(drafts)
            return {"ok": True, "status": "saved", "draft": draft}


class BusinessTools:
    def __init__(
        self,
        data_dir: str | Path,
        store_path: str | Path,
        failures: dict[str, str] | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.store = DraftStore(store_path)
        self.failures = failures or {}
        with (self.data_dir / "materials.json").open(encoding="utf-8") as handle:
            self.materials = {
                item["material_id"]: item for item in json.load(handle)["materials"]
            }
        with (self.data_dir / "rules.json").open(encoding="utf-8") as handle:
            self.rules = json.load(handle)
        with (self.data_dir / "schedule.json").open(encoding="utf-8") as handle:
            self.slots = json.load(handle)["slots"]

    def _injected_failure(self, tool_name: str) -> dict[str, Any] | None:
        if tool_name not in self.failures:
            return None
        return {
            "ok": False,
            "error_code": "injected_tool_failure",
            "message": self.failures[tool_name],
        }

    def get_material(self, material_id: str) -> dict[str, Any]:
        failure = self._injected_failure("get_material")
        if failure:
            return failure
        material = self.materials.get(material_id)
        if not material:
            return {
                "ok": False,
                "error_code": "material_not_found",
                "message": f"未找到素材 {material_id}。",
            }
        return {"ok": True, "material": copy.deepcopy(material)}

    def check_rules(self, material_id: str) -> dict[str, Any]:
        failure = self._injected_failure("check_rules")
        if failure:
            return failure
        material = self.materials.get(material_id)
        if not material:
            return {
                "ok": False,
                "error_code": "material_not_found",
                "message": f"未找到素材 {material_id}。",
            }
        missing = [
            field
            for field in self.rules["required_fields"]
            if material.get(field) is None or material.get(field) == ""
        ]
        labels = set(material.get("content_labels", []))
        risks = [
            {
                "rule_id": rule["rule_id"],
                "message": rule["message"],
                "severity": rule["severity"],
            }
            for rule in self.rules["rules"]
            if rule["label"] in labels
        ]
        if missing:
            status = "incomplete"
        elif risks:
            status = "blocked"
        else:
            status = "passed"
        return {
            "ok": True,
            "review_status": status,
            "missing_fields": missing,
            "risks": risks,
        }

    def query_slots(self, requested_start: str) -> dict[str, Any]:
        failure = self._injected_failure("query_slots")
        if failure:
            return failure
        requested = next(
            (slot for slot in self.slots if slot["start"] == requested_start), None
        )
        if not requested:
            return {
                "ok": False,
                "error_code": "slot_not_found",
                "message": "排期表中不存在所要求的时段。",
            }
        date = requested_start[:10]
        alternatives = [
            slot["start"]
            for slot in self.slots
            if slot["start"][:10] == date
            and slot["status"] == "available"
            and slot["start"] != requested_start
        ]
        return {
            "ok": True,
            "requested_start": requested_start,
            "requested_available": requested["status"] == "available",
            "occupied_by": requested.get("occupied_by"),
            "alternatives": alternatives,
        }

    def save_draft(
        self,
        request_id: str,
        material_id: str,
        requested_start: str,
        suggested_start: str,
    ) -> dict[str, Any]:
        failure = self._injected_failure("save_draft")
        if failure:
            return {**failure, "save_status": "failed"}
        slot = next((item for item in self.slots if item["start"] == suggested_start), None)
        if not slot:
            return {
                "ok": False,
                "save_status": "failed",
                "error_code": "slot_not_found",
                "message": "建议时段不存在，未保存草稿。",
            }
        if slot["status"] != "available":
            return {
                "ok": False,
                "save_status": "failed",
                "error_code": "slot_occupied",
                "message": "建议时段已占用，未保存草稿。",
            }
        confirmation = "pending" if suggested_start != requested_start else "not_required"
        payload = {
            "request_id": request_id,
            "material_id": material_id,
            "requested_start": requested_start,
            "suggested_start": suggested_start,
            "review_status": "passed",
            "confirmation_status": confirmation,
            "publish_status": "draft_only",
        }
        try:
            result = self.store.save(payload)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return {
                "ok": False,
                "save_status": "failed",
                "error_code": "storage_error",
                "message": f"草稿保存失败：{error}",
            }
        if not result["ok"]:
            return {**result, "save_status": "failed"}
        return {
            **result,
            "save_status": result["status"],
            "message": "草稿已保存。" if result["status"] == "saved" else "重复请求，返回原草稿。",
        }
