"""Unit tests for the ``template.properties`` passthrough contract.

The public property bag is a thin passthrough into the owning template adapter's
own structure (legacy flat applicationCoding vs nested template factory) — there
is no stable common DTO, so the contract is: unchanged passthrough, deep-copied,
and server-managed keys rejected.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.errors import (
    BotTemplateInvalidError,
)
from agentclaw.community.core.bot_management.create_flow import (
    to_internal_template_config,
)


def test_none_passes_through() -> None:
    assert to_internal_template_config(None) is None


def test_empty_dict_is_a_valid_passthrough() -> None:
    # An empty object is a legitimate (if minimal) template payload, and must
    # survive as {} rather than being collapsed to None by a truthiness check.
    assert to_internal_template_config({}) == {}


def test_payload_passes_through_unchanged_flat() -> None:
    payload = {
        "devflow_workflow": "app-flow",
        "yuque_kb_repos": [],
        "code_repos": [],
    }
    assert to_internal_template_config(payload) == payload


def test_payload_passes_through_unchanged_nested() -> None:
    payload = {
        "bot_template_config": {
            "preset_capabilities": {},
            "ext_config": {"thetaKey": "value"},
        }
    }
    assert to_internal_template_config(payload) == payload


def test_payload_is_deep_copied() -> None:
    payload = {"bot_template_config": {"ext_config": {"thetaKey": "v"}}}
    result = to_internal_template_config(payload)
    result["bot_template_config"]["ext_config"]["thetaKey"] = "mutated"
    assert payload["bot_template_config"]["ext_config"]["thetaKey"] == "v"


@pytest.mark.parametrize(
    "reserved",
    [
        "workspace_id",
        "template_uid",
        "bot_id",
        "workspace_status",
        "workspace_state",
        "start_status",
    ],
)
def test_server_reserved_field_is_rejected(reserved: str) -> None:
    with pytest.raises(BotTemplateInvalidError):
        to_internal_template_config({reserved: "x"})
