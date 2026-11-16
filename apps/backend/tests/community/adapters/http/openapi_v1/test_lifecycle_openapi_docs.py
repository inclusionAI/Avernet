"""Swagger lifecycle guidance must describe the existing wire behavior."""

from __future__ import annotations

import pytest

from agentclaw.community.adapters.http.openapi_v1.dormant.schemas import (
    BotActivateResult,
    BotRecycleOperation,
    BotRecycleResult,
)
from tests.community.adapters.http.openapi_v1.conftest import public_document

_BASE = "/openapi/v1/bots/{bot_id}"


def test_recycle_documents_admission_backup_policy_and_idempotency():
    operation = public_document()["paths"][f"{_BASE}/recycle"]["post"]
    description = operation["description"]
    for term in (
        "aicoding",
        "claude_code",
        "202",
        "operation_id",
        "changed=false",
        "fastdisk_fail_block",
        "1500",
        "NEEDS_CONFIRMATION",
        "502",
        "503",
    ):
        assert term in description
    assert "requestBody" not in operation
    responses = operation["responses"]
    for status in ("200", "202"):
        sample = responses[status]["content"]["application/json"]["example"]
        assert sample["code"] == int(status) * 1000
        BotRecycleResult.model_validate(sample["data"])
    assert "not completion" in responses["202"]["description"]


def test_recycle_query_documents_read_only_terminal_results():
    operation = public_document()["paths"][
        f"{_BASE}/recycle-operations/{{operation_id}}"
    ]["get"]
    description = operation["description"]
    for term in (
        "Read-only",
        "HTTP 200",
        "FAILED",
        "BACKUP_FAILED",
        "BACKUP_TIMEOUT",
        "NEEDS_CONFIRMATION",
        "404",
        "current/latest",
        "bot_status",
    ):
        assert term in description
    sample = operation["responses"]["200"]["content"]["application/json"]["example"]
    assert sample["code"] == 200000
    assert sample["data"]["phase"] == "FAILED"
    BotRecycleOperation.model_validate(sample["data"])


def test_activation_documents_recycle_conflicts_and_unchanged_polling():
    operation = public_document()["paths"][f"{_BASE}/activate"]["post"]
    description = operation["description"]
    for term in ("REACTIVATING", "ACTIVE", "NEEDS_CONFIRMATION", "409", "/status"):
        assert term in description
    assert "requestBody" not in operation
    for status in ("200", "202"):
        sample = operation["responses"][status]["content"]["application/json"][
            "example"
        ]
        assert sample["code"] == int(status) * 1000
        BotActivateResult.model_validate(sample["data"])


def test_container_restart_documents_http_200_async_progress_not_http_202():
    operation = public_document()["paths"][f"{_BASE}/restart"]["post"]
    description = operation["description"]
    for term in (
        "HTTP 200",
        "not HTTP 202",
        "ext.aicoding.restart",
        "operation_id",
        "QUEUED",
        "BACKING_UP",
        "RESTARTING",
        "WAITING_READY",
        "SUCCEEDED",
        "FAILED",
        "fastdisk_fail_block",
        "1500",
        "partial_success",
        "/status",
    ):
        assert term in description
    assert "requestBody" not in operation
    assert "200" in operation["responses"]
    assert "202" not in operation["responses"]
    assert "not completion" in operation["responses"]["200"]["description"]


def test_bot_status_documents_restart_extension_separately_from_readiness():
    operation = public_document()["paths"][f"{_BASE}/status"]["get"]
    for term in (
        "ext.aicoding.restart",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "phase",
        "is_ready",
        "backup_failed_continue",
        "recycle-operations",
    ):
        assert term in operation["description"]
    assert "200" in operation["responses"]


@pytest.mark.parametrize("model", ["BotRecycleResult", "BotRecycleOperation"])
def test_recycle_field_docs_distinguish_operation_and_bot_status(model):
    component = public_document()["components"]["schemas"][model]
    assert "NEEDS_CONFIRMATION" in component["properties"]["phase"]["description"]
    if model == "BotRecycleResult":
        assert "admission" in component["properties"]["changed"]["description"]
    else:
        assert "BACKUP_TIMEOUT" in component["properties"]["error_code"]["description"]
