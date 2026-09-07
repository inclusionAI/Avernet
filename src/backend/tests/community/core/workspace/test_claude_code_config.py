"""New Bot metadata selects initialization; legacy reads never invent defaults."""

import json
import pytest
from agentclaw.community.core.workspace.claude_code_config import (
    claude_code_workspace_from_ext,
    new_claude_code_ext,
    validate_claude_code_cwd,
)


@pytest.mark.parametrize("ext", [None, "", {}, {"unrelated": True}])
def test_legacy_has_no_initialization_selector(ext):
    assert claude_code_workspace_from_ext(ext) is None


def test_new_metadata_and_explicit_override_round_trip():
    original = {"unrelated": True}
    new = new_claude_code_ext(original)
    assert original == {"unrelated": True}
    assert claude_code_workspace_from_ext(json.dumps(new)) == "default"
    explicit = new_claude_code_ext({"claude_code_default_cwd": "/custom/workspace/"})
    assert claude_code_workspace_from_ext(explicit) == "/custom/workspace"


@pytest.mark.parametrize(
    "value",
    [None, "", "/", "relative", "/a/../b", "/a\n", "/$HOME", "/`pwd`", "/{home}"],
)
def test_invalid_explicit_paths_fail(value):
    with pytest.raises(ValueError):
        validate_claude_code_cwd(value)


@pytest.mark.parametrize(
    "ext", [[], "[]", {"claude_code_workspace_version": 2}, "not json"]
)
def test_invalid_metadata_fails(ext):
    with pytest.raises(ValueError):
        claude_code_workspace_from_ext(ext)
