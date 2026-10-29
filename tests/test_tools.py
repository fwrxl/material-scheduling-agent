from __future__ import annotations

import json

from src.tools import BusinessTools


def test_missing_fields_are_reported(tools: BusinessTools):
    result = tools.check_rules("M02")
    assert result["ok"] is True
    assert result["review_status"] == "incomplete"
    assert result["missing_fields"] == ["owner"]


def test_blocking_rule_is_reported(tools: BusinessTools):
    result = tools.check_rules("M03")
    assert result["review_status"] == "blocked"
    assert result["risks"][0]["rule_id"] == "R01"


def test_slot_query_exposes_conflict_and_alternatives(tools: BusinessTools):
    result = tools.query_slots("2027-04-10T18:00:00+08:00")
    assert result["requested_available"] is False
    assert result["occupied_by"] == "EXISTING-01"
    assert result["alternatives"] == [
        "2027-04-10T19:00:00+08:00",
        "2027-04-10T20:00:00+08:00",
    ]


def test_occupied_slot_cannot_be_saved(tools: BusinessTools):
    result = tools.save_draft(
        "REQ-OCCUPIED",
        "M01",
        "2027-04-10T18:00:00+08:00",
        "2027-04-10T18:00:00+08:00",
    )
    assert result["ok"] is False
    assert result["error_code"] == "slot_occupied"
    assert not tools.store.path.exists()


def test_duplicate_save_returns_same_draft(tools: BusinessTools):
    arguments = {
        "request_id": "REQ-SAME",
        "material_id": "M01",
        "requested_start": "2027-04-10T19:00:00+08:00",
        "suggested_start": "2027-04-10T19:00:00+08:00",
    }
    first = tools.save_draft(**arguments)
    second = tools.save_draft(**arguments)
    assert first["status"] == "saved"
    assert second["status"] == "duplicate"
    assert second["draft"]["draft_id"] == first["draft"]["draft_id"]
    assert len(json.loads(tools.store.path.read_text(encoding="utf-8"))) == 1


def test_same_request_id_with_different_payload_is_rejected(tools: BusinessTools):
    first = tools.save_draft(
        "REQ-CONFLICTING-PAYLOAD",
        "M01",
        "2027-04-10T19:00:00+08:00",
        "2027-04-10T19:00:00+08:00",
    )
    second = tools.save_draft(
        "REQ-CONFLICTING-PAYLOAD",
        "M01",
        "2027-04-10T18:00:00+08:00",
        "2027-04-10T19:00:00+08:00",
    )
    assert first["ok"] is True
    assert second["ok"] is False
    assert second["error_code"] == "idempotency_conflict"


def test_save_failure_does_not_create_store(data_dir, tmp_path):
    tools = BusinessTools(
        data_dir,
        tmp_path / "drafts.json",
        failures={"save_draft": "injected"},
    )
    result = tools.save_draft(
        "REQ-FAIL",
        "M01",
        "2027-04-10T19:00:00+08:00",
        "2027-04-10T19:00:00+08:00",
    )
    assert result["save_status"] == "failed"
    assert not tools.store.path.exists()


def test_malformed_store_returns_failure_not_success(data_dir, tmp_path):
    path = tmp_path / "drafts.json"
    path.write_text("not-json", encoding="utf-8")
    tools = BusinessTools(data_dir, path)
    result = tools.save_draft(
        "REQ-BROKEN",
        "M01",
        "2027-04-10T19:00:00+08:00",
        "2027-04-10T19:00:00+08:00",
    )
    assert result["ok"] is False
    assert result["save_status"] == "failed"
    assert result["error_code"] == "storage_error"
