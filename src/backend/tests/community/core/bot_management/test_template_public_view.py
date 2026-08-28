"""Allowlist projection of ``template_config`` for public responses.

``ac_templates.ext`` legitimately stores engine secrets: the aicoding
provisioning strategy persists ``bot_template_config.ext_config.thetaKey`` as
an ``enc:v1:`` ciphertext, and the stable outer contract allows a plain
``token``. The public listing faces must never echo either, and engine-owned
extensions must default to "not surfaced" rather than "passed through" — this
matrix pins that.
"""

from __future__ import annotations

import pytest

from agentclaw.community.core.bot_management.template_public_view import (
    project_template_config_for_public,
)


def test_none_in_gives_none_out():
    assert project_template_config_for_public(None) is None


def test_empty_dict_gives_none_out():
    assert project_template_config_for_public({}) is None


def test_allowlisted_keys_survive():
    config = {
        "devflow_workflow": "release-notes",
        "yuque_kb_repos": ["team/kb"],
        "code_repos": ["team/svc"],
        "template_key": "normalCC",
        "template_uid": "tpl-1",
    }
    assert project_template_config_for_public(config) == config


@pytest.mark.parametrize(
    "secret_config",
    [
        {"devflow_workflow": "w", "token": "Bearer raw-secret"},
        {
            "devflow_workflow": "w",
            "bot_template_config": {"ext_config": {"thetaKey": "enc:v1:deadbeef"}},
        },
        {"devflow_workflow": "w", "runtime": "codefuse"},
        {"devflow_workflow": "w", "anything_engine_owned": {"nested": "blob"}},
    ],
)
def test_everything_else_is_dropped(secret_config):
    projected = project_template_config_for_public(secret_config)
    assert projected == {"devflow_workflow": "w"}


def test_projection_of_only_secrets_gives_none():
    config = {"token": "Bearer raw-secret"}
    assert project_template_config_for_public(config) is None


def test_result_is_not_aliased_with_input():
    config = {"devflow_workflow": "w", "yuque_kb_repos": ["a"]}
    projected = project_template_config_for_public(config)
    projected["yuque_kb_repos"].append("mutated")
    projected["devflow_workflow"] = "mutated"
    assert config["yuque_kb_repos"] == ["a"]
    assert config["devflow_workflow"] == "w"
