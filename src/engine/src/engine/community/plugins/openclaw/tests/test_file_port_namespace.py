"""Namespace-relative addressing for the OpenClaw file port (#1000).

``/api/file/*`` accepts two address formats; these tests pin the new one and
prove the old one is untouched:

* ``workspace/<rel>`` · ``identity/<rel>`` · ``config/<rel>`` resolve against
  this engine's own layout, and cannot address outside their namespace.
* OSS-view and engine-view absolute paths resolve exactly as before — the
  discriminator is the first path segment, so nothing that works today changes.
* A bare relative path is refused instead of resolving against the engine
  process's CWD, which is the silent data loss #1000 reported.

The port methods are driven directly on ``OpenClawPluginImpl`` (no gateway or
pool needed), with ``OPENCLAW_WORKSPACE_DIR`` pointed at ``tmp_path``.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.community.plugins.openclaw._file import _convert_path
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


@pytest.fixture
def impl():
    return OpenClawPluginImpl()


@pytest.fixture
def workspace(tmp_path):
    """A per-bot engine layout: ``{engine_dir}/workspace``, exported via env.

    Mirrors what baas injects at spawn time, so the namespace roots resolve the
    way they do on a real singlebox host.
    """
    root = tmp_path / "bolt_data" / "staff_1" / "bot-1" / "openclaw" / "workspace"
    root.mkdir(parents=True)
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": str(root)}):
        yield root


# ── _convert_path: namespace resolution ──────────────────────────────────────


def test_workspace_namespace_resolves_under_workspace_root(workspace):
    assert _convert_path("workspace/docs/a.txt") == workspace / "docs" / "a.txt"


def test_workspace_namespace_tolerates_leading_slash(workspace):
    """Engines accept ``/workspace/<rel>``; the backend only emits the bare form."""
    assert _convert_path("/workspace/docs/a.txt") == workspace / "docs" / "a.txt"


def test_bare_workspace_namespace_is_the_workspace_root(workspace):
    """``list_dir`` addresses the root as the bare namespace (no relative part)."""
    assert _convert_path("workspace") == workspace


def test_identity_namespace_shares_the_workspace_root(workspace):
    """openclaw keeps identity files in the workspace root, not a separate dir.

    Matches the backend's ``build_arca_identity_mapper``, which composes
    ``{engine_dir}/workspace/<file>`` for openclaw.
    """
    assert _convert_path("identity/AGENTS.md") == workspace / "AGENTS.md"


def test_config_namespace_resolves_beside_the_workspace(workspace):
    """The engine config lives at ``{engine_dir}/openclaw.json``.

    Same file the OSS-view address ``…/<bot>/openclaw/openclaw.json`` folds to.
    """
    assert _convert_path("config/openclaw.json") == workspace.parent / "openclaw.json"


def test_config_namespace_spellings_agree(workspace):
    assert _convert_path("config") == _convert_path("config/openclaw.json")


def test_config_namespace_and_oss_view_address_the_same_file(workspace):
    oss = (
        "/aidesktop/aidesktop_singlebox/bolt_data/staff_1/bot-1/openclaw/openclaw.json"
    )
    assert _convert_path(oss) == _convert_path("config")


def test_bare_config_namespace_is_the_engine_config_file(workspace):
    """``config`` holds one file, so the bare namespace names it directly.

    That spelling is what lets the backend address the config without knowing
    any engine's filename.
    """
    assert _convert_path("config") == workspace.parent / "openclaw.json"


@pytest.mark.parametrize(
    "target",
    ["config/teclaw.json", "config/config.json", "config/sub/openclaw.json"],
)
def test_config_namespace_refuses_a_foreign_leaf(workspace, target):
    """The backend addresses every provider's config as ``config/teclaw.json``
    today and lets its arca/baas mapper derive the real filename. Writing that
    leaf verbatim would drop a stray file beside the real config and report
    success — silent, and exactly the failure #1000 is about."""
    with pytest.raises(ValueError, match="holds this engine's config file"):
        _convert_path(target)


def test_namespace_normalizes_noise_segments(workspace):
    """Empty and ``.`` segments are noise, not an escape attempt."""
    assert _convert_path("workspace//docs/./a.txt") == workspace / "docs" / "a.txt"


def test_namespace_strips_surrounding_whitespace(workspace):
    assert _convert_path("  workspace/a.txt  ") == workspace / "a.txt"


def test_namespace_and_oss_view_address_the_same_file(workspace):
    """Both wire formats must land on one file — that is what makes the
    backend's cutover a no-op for existing data."""
    oss = (
        "/aidesktop/aidesktop_singlebox/bolt_data/staff_1/bot-1/openclaw/"
        "workspace/docs/a.txt"
    )
    assert _convert_path(oss) == _convert_path("workspace/docs/a.txt")


# ── _convert_path: containment ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "target",
    [
        "workspace/../../etc/passwd",
        "identity/../../../etc/passwd",
        "config/../secrets.json",
        "/workspace/../escape.txt",
        "workspace/docs/../../escape.txt",
    ],
)
def test_namespace_escape_raises_value_error(workspace, target):
    with pytest.raises(ValueError, match="escapes the"):
        _convert_path(target)


def test_escape_error_names_the_namespace(workspace):
    with pytest.raises(ValueError, match="'identity'"):
        _convert_path("identity/../x")


# ── _convert_path: env handling ──────────────────────────────────────────────


def test_namespace_without_env_falls_back_to_home_layout(tmp_path):
    """No ``OPENCLAW_WORKSPACE_DIR`` → the engine's default ``~/.openclaw``.

    Same root ``workspace_root()`` hands every other engine service, so a file
    uploaded as ``workspace/<rel>`` is the file those services then see.
    """
    env = {k: v for k, v in os.environ.items() if k != "OPENCLAW_WORKSPACE_DIR"}
    env["HOME"] = str(tmp_path)
    with patch.dict(os.environ, env, clear=True):
        engine_dir = tmp_path / ".openclaw"
        assert _convert_path("workspace/a.txt") == engine_dir / "workspace" / "a.txt"
        assert _convert_path("config") == engine_dir / "openclaw.json"


def test_namespace_with_relative_env_raises_runtime_error():
    """A relative ``OPENCLAW_WORKSPACE_DIR`` is a spawn-time config error.

    Same guard the singlebox OSS-view branch applies — resolving it against the
    process CWD is exactly the silent misplacement being removed.
    """
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": "relative/openclaw/workspace"}):
        with pytest.raises(RuntimeError, match="must be an absolute path"):
            _convert_path("workspace/a.txt")


# ── _convert_path: the absolute formats are untouched ────────────────────────


def test_engine_view_absolute_path_still_passes_through(workspace):
    """Callers pass hardcoded container paths; the discriminator must not eat
    them. ``/home/admin/.openclaw/workspace`` contains a ``workspace`` segment
    but does not *start* with one."""
    target = "/home/admin/.openclaw/workspace/skills"
    assert _convert_path(target) == Path(target)


def test_prod_oss_view_path_still_folds_to_hardcoded_root(workspace):
    """Branch 1 runs before the namespace branch and is env-independent."""
    result = _convert_path(
        "/aidesktop/aidesktop_prod/bolt_data/X/Y/openclaw/workspace/a.md"
    )
    assert result == Path("/home/admin/.openclaw/workspace/a.md")


@pytest.mark.parametrize(
    "target",
    ["Workspace/a.txt", "workspaces/a.txt", "work/space/a.txt"],
)
def test_near_miss_prefixes_are_not_namespaces(workspace, target):
    """The namespace set is matched exactly — no case folding, no prefixing."""
    with pytest.raises(ValueError, match="relative path without an engine namespace"):
        _convert_path(target)


# ── _convert_path: bare relative paths are refused ───────────────────────────


@pytest.mark.parametrize("target", ["111.txt", "sub/dir/111.txt", "./111.txt"])
def test_bare_relative_path_raises_value_error(workspace, target):
    """#1000: these used to resolve against the engine process's CWD."""
    with pytest.raises(ValueError, match="relative path without an engine namespace"):
        _convert_path(target)


def test_relative_rejection_names_the_namespaces(workspace):
    with pytest.raises(ValueError, match="workspace/, identity/, config/"):
        _convert_path("111.txt")


# ── port methods over namespace addresses ────────────────────────────────────


async def test_upload_namespace_path_lands_in_the_workspace(impl, workspace):
    result = await impl.upload("workspace/docs/spec/a.txt", b"hello")

    written = workspace / "docs" / "spec" / "a.txt"
    assert written.read_bytes() == b"hello"
    assert result["target_path"] == str(written)
    assert result["size"] == 5
    assert result["overwritten"] is False


async def test_upload_namespace_path_echoes_an_absolute_target(impl, workspace):
    """The echoed ``target_path`` is absolute, never the relative input.

    The backend keys its rollout safety on exactly this: an engine that has not
    learned the format echoes the relative path back, and the backend refuses
    the response instead of recording a resource whose bytes went nowhere.
    """
    result = await impl.upload("workspace/a.txt", b"x")

    assert Path(result["target_path"]).is_absolute()
    assert result["target_path"] != "workspace/a.txt"


async def test_upload_namespace_path_overwrites(impl, workspace):
    (workspace / "a.txt").write_bytes(b"old")

    result = await impl.upload("workspace/a.txt", b"new")

    assert result["overwritten"] is True
    assert (workspace / "a.txt").read_bytes() == b"new"


async def test_upload_identity_namespace_writes_workspace_root_file(impl, workspace):
    await impl.upload("identity/AGENTS.md", b"# agents")

    assert (workspace / "AGENTS.md").read_bytes() == b"# agents"


async def test_upload_config_namespace_writes_beside_the_workspace(impl, workspace):
    await impl.upload("config/openclaw.json", b"{}")

    assert (workspace.parent / "openclaw.json").read_bytes() == b"{}"


async def test_upload_bare_config_namespace_writes_the_engine_config(impl, workspace):
    await impl.upload("config", b'{"a": 1}')

    assert (workspace.parent / "openclaw.json").read_bytes() == b'{"a": 1}'


async def test_upload_foreign_config_leaf_writes_nothing(impl, workspace):
    with pytest.raises(ValueError, match="holds this engine's config file"):
        await impl.upload("config/teclaw.json", b"{}")

    assert not (workspace.parent / "teclaw.json").exists()


async def test_read_round_trip_over_namespace_path(impl, workspace):
    await impl.upload("workspace/rt.bin", b"\xde\xad\xbe\xef")

    assert await impl.read("workspace/rt.bin") == b"\xde\xad\xbe\xef"


async def test_remove_over_namespace_path(impl, workspace):
    (workspace / "gone.txt").write_bytes(b"x")

    result = await impl.remove("workspace/gone.txt")

    assert result["path_type"] == "file"
    assert not (workspace / "gone.txt").exists()


async def test_rmtree_over_namespace_path(impl, workspace):
    tree = workspace / "tree"
    (tree / "nested").mkdir(parents=True)
    (tree / "nested" / "a.txt").write_bytes(b"x")

    out = await impl.rmtree("workspace/tree")

    assert out == str(tree)
    assert not tree.exists()


async def test_list_dir_over_bare_workspace_namespace(impl, workspace):
    (workspace / "a.txt").write_bytes(b"1")
    (workspace / "sub").mkdir()

    result = await impl.list_dir("workspace")

    assert result["dir_path"] == str(workspace)
    assert sorted(e["name"] for e in result["files"]) == ["a.txt", "sub"]


async def test_list_dir_over_namespace_subdirectory(impl, workspace):
    sub = workspace / "docs"
    sub.mkdir()
    (sub / "a.txt").write_bytes(b"1")

    result = await impl.list_dir("workspace/docs")

    assert [e["relative_path"] for e in result["files"]] == ["a.txt"]


async def test_namespace_read_follows_a_skill_symlink_out_of_the_workspace(
    impl, workspace, tmp_path
):
    """Containment is lexical, and has to be: the skills bindpaths symlink
    ``workspace/skills/skills-local`` into the pool, which lives outside the
    workspace. Resolving symlinks before the containment check would make every
    skill file look like an escape."""
    pool = tmp_path / "skills-pool" / "local"
    pool.mkdir(parents=True)
    (pool / "SKILL.md").write_bytes(b"# skill")
    skills = workspace / "skills"
    skills.mkdir()
    (skills / "skills-local").symlink_to(pool, target_is_directory=True)

    assert await impl.read("workspace/skills/skills-local/SKILL.md") == b"# skill"


async def test_upload_escaping_namespace_path_writes_nothing(impl, workspace, tmp_path):
    outside = tmp_path / "outside.txt"

    with pytest.raises(ValueError, match="escapes the"):
        await impl.upload("workspace/../../../../outside.txt", b"x")

    assert not outside.exists()


async def test_upload_bare_relative_path_is_refused(impl, workspace):
    """#1000's exact reproduction: a bare filename used to be written to the
    engine process's CWD and reported as a success."""
    with pytest.raises(ValueError, match="relative path without an engine namespace"):
        await impl.upload("111.txt", b"x")

    assert not (workspace / "111.txt").exists()
    assert not (Path.cwd() / "111.txt").exists()
