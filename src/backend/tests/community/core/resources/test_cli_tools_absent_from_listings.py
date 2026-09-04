"""CLI tools stay out of the resources surface (W9, #1477).

This file pins a **property that nothing was built to enforce**, which is
exactly why it needs a test rather than a comment.

The reasoning: the resources API is confined to the workspace namespace by
construction. Every operation composes its device path through
``ResourceFileService._logical``, which prefixes ``workspace/``; the mapper
raises for anything outside it; and ``safe_workspace_path`` refuses ``..``.
A CLI tool is not addressed by path at all — the platform installs it *by name*
and the engine decides where it lands, which the delivery port's own tests pin
— so there is no path the resources surface could be asked for that would
reach one.

That makes the isolation structural. The risk is that a later change makes it
untrue by accident: a tools directory placed under the workspace, a filter
added and then removed, a namespace widened. These tests fail if it does.

W9 therefore modified **no resources file**, and the last test says so.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from agentclaw.community.core.bot_config_manifest.cli_tools import (
    CliToolScope,
    CliToolStore,
)
from agentclaw.community.core.bot_config_manifest.cli_tools import (
    arca_port,
    teclaw_port,
)
from agentclaw.community.core.services import resource_file_service

_BACKEND_ROOT = Path(__file__).resolve().parents[4]


def test_the_resources_surface_only_ever_addresses_the_workspace() -> None:
    """The mechanism the isolation rests on, asserted rather than assumed."""
    assert resource_file_service.ResourceFileService._logical("") == "workspace"
    assert resource_file_service.ResourceFileService._logical("a/b") == "workspace/a/b"


def test_no_cli_tool_is_addressed_by_a_path_the_resources_surface_could_name() -> None:
    """The platform installs a tool **by name**; the engine chooses the
    directory. There is no path to collide with, in either direction."""
    for module in (arca_port, teclaw_port):
        source = inspect.getsource(module)
        assert "workspace" not in source, f"{module.__name__} names the workspace"


def test_a_tools_object_key_is_not_under_a_workspace_namespace() -> None:
    """The platform's own copy lives under its own prefix in the object store.

    Not a container path at all — but if it were ever placed under the
    ``workspace`` namespace of the promotion layout, a teclaw bot's next
    promotion would sweep it into the artifact's ``resources``, and the file
    surface would list it."""
    store = CliToolStore(
        object_storage=object(), store_base=lambda: "teclaw/dev/bolt_data"
    )
    scope = CliToolScope(entity_type="staff", entity_id="u1", bot_id="bot7")
    live = store.store_key(scope, "mycli", "sha256:" + "ab" * 32)
    staged = store.stage_store_key(
        scope, name="mycli", publish_id=9, stage="verify"
    )
    for key in (live, staged):
        assert "/workspace/" not in key and not key.endswith("/workspace")
        assert "/identity/" not in key


def test_the_hidden_dirname_filter_was_not_used_and_is_unchanged() -> None:
    """Recorded because it was the obvious wrong answer.

    ``_HIDDEN_DIRNAMES`` guards the **root listing only** — the check is
    ``if is_dir and not path and name in _HIDDEN_DIRNAMES`` — so a name hidden
    there is still reachable one directory down. Relying on it would have made
    the isolation a filter with a hole rather than a property. W9 added nothing
    to it.
    """
    assert "cli" not in resource_file_service._HIDDEN_DIRNAMES
    assert "cli-tools" not in resource_file_service._HIDDEN_DIRNAMES
    assert "cli_tools" not in resource_file_service._HIDDEN_DIRNAMES


def test_no_resources_or_path_mapping_file_was_modified_by_this_feature() -> None:
    """The strongest form of the claim: the isolation needed no code.

    Named files rather than a diff walk, because the property is "these files
    contain nothing about CLI tools", and that is checkable from their content
    at any point in their history.
    """
    untouched = [
        "src/agentclaw/community/core/services/resource_file_service.py",
        "src/agentclaw/community/core/services/resource_addressing.py",
        "src/agentclaw/community/core/config_compose/teclaw_paths.py",
    ]
    for relative in untouched:
        text = (_BACKEND_ROOT / relative).read_text(encoding="utf-8")
        assert "cli_tool" not in text and "cli-tools" not in text, (
            f"{relative} mentions CLI tools: the resources surface was supposed "
            "to need no change, so either the isolation stopped being structural "
            "or a filter crept in"
        )
