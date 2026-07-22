"""JSON-parity tests for the neutral kernel device DTOs (B6).

These pin the serialized wire shape that the BaaS endpoints expect. The shapes
were captured from the prior ``arca.model.sandbox`` carriers as hand-serialized
by ``core/service_bot/services/baas_service.py`` (the create-bot payload reads
the DTO attributes directly). A drift here would silently break corp bot-create,
so the expected dicts below are the contract.
"""
from __future__ import annotations

import pytest

from agentclaw.community.kernel.device_dto import (
    CommandResult,
    HeaderOperationRule,
    OutBoundOperationRule,
    ResourceSpecification,
)


pytestmark = pytest.mark.unit


def test_resource_spec_to_dict_omits_disk_when_none() -> None:
    assert ResourceSpecification(cpu=2, memory=4096).to_dict() == {
        "cpu": 2,
        "memory": 4096,
    }


def test_resource_spec_to_dict_includes_disk_when_set() -> None:
    assert ResourceSpecification(cpu=4, memory=8192, disk=100).to_dict() == {
        "cpu": 4,
        "memory": 8192,
        "disk": 100,
    }


def test_header_operation_rule_to_dict_full_shape() -> None:
    rule = HeaderOperationRule(
        domains=["antchat.teamclaw.com"],
        action="replace",
        header_name="Authorization",
        value="tok",
        placeholder="${API-KEY}",
    )
    assert rule.to_dict() == {
        "domains": ["antchat.teamclaw.com"],
        "action": "replace",
        "header_name": "Authorization",
        "value": "tok",
        "placeholder": "${API-KEY}",
        "separator": None,
    }


def test_header_operation_rule_placeholder_defaults_to_none() -> None:
    rule = HeaderOperationRule(
        domains=["bcn.teamclaw.com"],
        action="set",
        header_name="x-id",
        value="v",
    )
    assert rule.to_dict()["placeholder"] is None


def test_outbound_rule_to_dict_nests_header_rules() -> None:
    rule = OutBoundOperationRule(
        header_operation_rules=[
            HeaderOperationRule(
                domains=["d1"], action="set", header_name="h1", value="v1"
            ),
            HeaderOperationRule(
                domains=["d2"], action="replace", header_name="h2", value="v2",
                placeholder="${P}",
            ),
        ]
    )
    assert rule.to_dict() == {
        "header_operation_rules": [
            {
                "domains": ["d1"],
                "action": "set",
                "header_name": "h1",
                "value": "v1",
                "placeholder": None,
                "separator": None,
            },
            {
                "domains": ["d2"],
                "action": "replace",
                "header_name": "h2",
                "value": "v2",
                "placeholder": "${P}",
                "separator": None,
            },
        ]
    }


def test_outbound_rule_default_is_empty() -> None:
    assert OutBoundOperationRule().to_dict() == {"header_operation_rules": []}


def test_command_result_to_dict_full_shape() -> None:
    result = CommandResult(
        stdout="out",
        stderr="err",
        exit_code=1,
        elapsed_time=0.5,
        status="error",
        error="boom",
        extra={"k": "v"},
    )
    assert result.to_dict() == {
        "stdout": "out",
        "stderr": "err",
        "exit_code": 1,
        "elapsed_time": 0.5,
        "status": "error",
        "error": "boom",
        "extra": {"k": "v"},
    }


def test_command_result_from_dict_coerces_and_defaults() -> None:
    result = CommandResult.from_dict(
        {"stdout": None, "exit_code": "3", "extra": {"a": 1}}
    )
    assert result.stdout == ""
    assert result.exit_code == 3
    assert result.elapsed_time == 0.0
    assert result.error is None
    assert result.extra == {"a": "1"}


def test_command_result_roundtrip() -> None:
    original = CommandResult(
        stdout="o", stderr="e", exit_code=0, elapsed_time=1.25,
        status="completed", error=None, extra={"x": "y"},
    )
    assert CommandResult.from_dict(original.to_dict()) == original
