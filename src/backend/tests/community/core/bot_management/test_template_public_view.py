"""Verbatim passthrough of ``template_config`` for public responses.

``ac_templates.ext`` stores the template snapshot exactly as the creation
input supplied it. Decision (2026-09-01): the public query faces return that
snapshot **verbatim** — the caller is the owner (or a delegate the owner
authorized), so echoing ``token`` / ``bot_template_config.ext_config.thetaKey``
is echoing the caller's own input rather than disclosing a secret. The allowlist
projection this module used to enforce was removed deliberately; this matrix
pins the passthrough contract plus the one remaining rule: the result is a
fresh deep copy, never an alias of the stored snapshot.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.template_public_view import (
    template_config_for_public,
)


def test_none_in_gives_none_out():
    assert template_config_for_public(None) is None


def test_non_mapping_in_gives_none_out():
    assert template_config_for_public("not-a-mapping") is None
    assert template_config_for_public([("k", "v")]) is None


def test_empty_dict_is_returned_verbatim():
    # No truthiness collapsing: an empty stored object is an empty object,
    # not a missing one.
    assert template_config_for_public({}) == {}


def test_snapshot_passes_through_unchanged():
    config = {
        "devflow_workflow": "release-notes",
        "devflow_workflows": [{"path": "release-notes"}, {"path": "trivial"}],
        "yuque_kb_repos": ["team/kb"],
        "code_repos": ["team/svc"],
        "template_key": "normalCC",
        "template_uid": "tpl-1",
        "model": "qwen",
        "runtime": "codefuse",
        "engine_form": "aicoding",
        "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:deadbeef"}},
        "token": "Bearer raw-secret",
    }
    assert template_config_for_public(config) == config


@pytest.mark.parametrize(
    "secret_config",
    [
        {"devflow_workflow": "w", "token": "Bearer raw-secret"},
        {
            "devflow_workflow": "w",
            "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:deadbeef"}},
        },
    ],
)
def test_secret_fields_are_echoed_to_the_owner(secret_config):
    # The 2026-09-01 decision: owner-scoped query faces echo the caller's own
    # creation input, secrets included. Pinned so a future revert is a
    # conscious, logged decision rather than silent drift.
    assert template_config_for_public(secret_config) == secret_config


def test_result_is_not_aliased_with_input():
    config = {
        "devflow_workflow": "w",
        "yuque_kb_repos": ["a"],
        "bot_template_config": {"ext_config": {"thetaKey": "v"}},
    }
    result = template_config_for_public(config)
    result["yuque_kb_repos"].append("mutated")
    result["devflow_workflow"] = "mutated"
    result["bot_template_config"]["ext_config"]["thetaKey"] = "mutated"
    assert config["yuque_kb_repos"] == ["a"]
    assert config["devflow_workflow"] == "w"
    assert config["bot_template_config"]["ext_config"]["thetaKey"] == "v"
