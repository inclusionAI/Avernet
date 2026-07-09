"""Port-impl tests for OpenClawPluginImpl file methods (upload/read/remove/rmtree/list_dir).

Drives the file methods directly on OpenClawPluginImpl against tmp_path so no
gateway or pool is needed.  The _convert_path rewrite + passthrough branches
are exercised through module-level import of the helper.

Raw dict/bytes returns are asserted — DTO builds live in the adapter tests.
Preserves full coverage from legacy engines/openclaw/tests/test_file.py.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from engine.community.plugins.openclaw._file import _convert_path
from engine.community.plugins.openclaw.plugin_impl import OpenClawPluginImpl


# ── _convert_path unit tests ──────────────────────────────────────────────────


def test_convert_path_translates_legacy_pre_prefix():
    p = _convert_path(
        "/aidesktop/aidesktop_pre/bolt_data/staff_42/proj/openclaw/workspace/foo.txt"
    )
    assert str(p).startswith("/home/admin/.openclaw/")
    assert str(p).endswith("workspace/foo.txt")


def test_convert_path_translates_legacy_prod_prefix():
    p = _convert_path(
        "/aidesktop/aidesktop_prod/bolt_data/user/bot/openclaw/data/img.png"
    )
    assert str(p).startswith("/home/admin/.openclaw/")
    assert str(p).endswith("data/img.png")


def test_convert_path_passthrough_when_no_match():
    p = _convert_path("/tmp/already/normal/path.txt")
    assert str(p) == "/tmp/already/normal/path.txt"


def test_convert_path_passthrough_home_admin_prefix():
    """agentbox/desktop链路 — path already uses engine-view prefix."""
    p = _convert_path("/home/admin/.openclaw/workspace/myfile.txt")
    assert str(p) == "/home/admin/.openclaw/workspace/myfile.txt"


def test_convert_path_strips_whitespace():
    p = _convert_path("  /tmp/x.txt  ")
    assert str(p) == "/tmp/x.txt"


# ── _convert_path: extended branches (ported from tests/plugins/prod/openclaw) ──
# These cover the singlebox env-folding + RuntimeError paths that the basic
# tests above do not exercise.


def test_prod_oss_view_path_folds_to_engine_view():
    """线上 prod /aidesktop/aidesktop_prod/bolt_data/... → /home/admin/.openclaw/..."""
    env_without = {k: v for k, v in os.environ.items() if k != "OPENCLAW_WORKSPACE_DIR"}
    with patch.dict(os.environ, env_without, clear=True):
        result = _convert_path(
            "/aidesktop/aidesktop_prod/bolt_data/staff_168944/bot-x/openclaw/workspace/skills/foo.txt"
        )
    assert result == Path("/home/admin/.openclaw/workspace/skills/foo.txt")


def test_prod_branch_unaffected_by_env(tmp_path):
    """即使 OPENCLAW_WORKSPACE_DIR 设了,线上 OSS-view 路径仍按原 hardcode 翻译"""
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": str(tmp_path)}):
        result = _convert_path(
            "/aidesktop/aidesktop_prod/bolt_data/X/Y/openclaw/workspace/a.md"
        )
    assert result == Path("/home/admin/.openclaw/workspace/a.md")


def test_singlebox_with_env_folds_to_per_bot_path(tmp_path):
    """singlebox /aidesktop/aidesktop_singlebox/bolt_data/... + env 设了 → 折叠到 env.parent + 残段"""
    env_root = tmp_path / "bolt_data" / "staff_X" / "bot-Y" / "openclaw" / "workspace"
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": str(env_root)}):
        result = _convert_path(
            "/aidesktop/aidesktop_singlebox/bolt_data/staff_168944/bot-Z/openclaw/workspace/skills/foo.txt"
        )
    assert result == env_root.parent / "workspace" / "skills" / "foo.txt"


def test_singlebox_without_env_raises_runtime_error():
    """singlebox regex 匹配但 env 未设 → 显式 RuntimeError(配置错误)"""
    env_without = {k: v for k, v in os.environ.items() if k != "OPENCLAW_WORKSPACE_DIR"}
    with patch.dict(os.environ, env_without, clear=True):
        with pytest.raises(RuntimeError, match="OPENCLAW_WORKSPACE_DIR not set"):
            _convert_path(
                "/aidesktop/aidesktop_singlebox/bolt_data/X/Y/openclaw/workspace/foo"
            )


def test_singlebox_with_empty_env_raises_runtime_error():
    """env 是空字符串 → workspace_root_strict 返 None → 同样 RuntimeError"""
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": ""}):
        with pytest.raises(RuntimeError, match="OPENCLAW_WORKSPACE_DIR not set"):
            _convert_path(
                "/aidesktop/aidesktop_singlebox/bolt_data/X/Y/openclaw/workspace/foo"
            )


def test_singlebox_with_relative_env_raises_runtime_error():
    """env 是相对路径 → workspace_root_strict 返回相对 Path → RuntimeError(配置错误)"""
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": "relative/path/openclaw/workspace"}):
        with pytest.raises(RuntimeError, match="must be an absolute path"):
            _convert_path(
                "/aidesktop/aidesktop_singlebox/bolt_data/X/Y/openclaw/workspace/foo"
            )


def test_singlebox_env_root_preserves_kernel_path_segments(tmp_path):
    """验证 OSS-view 路径中 openclaw/ 后面的残段被逐字带过去。"""
    env_root = tmp_path / "host" / "staff_42" / "bot-7" / "openclaw" / "workspace"
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": str(env_root)}):
        result = _convert_path(
            "/aidesktop/aidesktop_singlebox/bolt_data/staff_42/bot-7/openclaw/workspace/resources/avatars/01.png"
        )
    assert result == env_root.parent / "workspace" / "resources" / "avatars" / "01.png"
    assert str(result) == str(env_root.parent / "workspace" / "resources" / "avatars" / "01.png")


# ── fixture ───────────────────────────────────────────────────────────────────


@pytest.fixture
def impl():
    return OpenClawPluginImpl()


# ── upload ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_creates_parent_dirs_and_file(impl, tmp_path):
    target = tmp_path / "nested" / "deep" / "file.bin"
    result = await impl.upload(str(target), b"hello world")
    assert target.exists()
    assert target.read_bytes() == b"hello world"
    assert result["size"] == 11
    assert result["overwritten"] is False
    assert result["target_path"] == str(target)


@pytest.mark.asyncio
async def test_upload_overwrite_existing_file(impl, tmp_path):
    target = tmp_path / "f.txt"
    target.write_text("old content")
    result = await impl.upload(str(target), b"new content")
    assert result["overwritten"] is True
    assert target.read_bytes() == b"new content"
    assert result["size"] == 11


@pytest.mark.asyncio
async def test_upload_empty_path_raises_value_error(impl):
    with pytest.raises(ValueError):
        await impl.upload("", b"x")


@pytest.mark.asyncio
async def test_upload_whitespace_only_path_raises_value_error(impl):
    with pytest.raises(ValueError):
        await impl.upload("   ", b"x")


@pytest.mark.asyncio
async def test_upload_directory_collision_raises(impl, tmp_path):
    target = tmp_path / "adir"
    target.mkdir()
    with pytest.raises(IsADirectoryError):
        await impl.upload(str(target), b"x")


@pytest.mark.asyncio
async def test_upload_returns_correct_size(impl, tmp_path):
    target = tmp_path / "blob.bin"
    content = b"\x00" * 256
    result = await impl.upload(str(target), content)
    assert result["size"] == 256


# ── read ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_returns_bytes(impl, tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"data bytes")
    assert await impl.read(str(f)) == b"data bytes"


@pytest.mark.asyncio
async def test_read_empty_path_returns_empty_bytes(impl):
    result = await impl.read("")
    assert result == b""


@pytest.mark.asyncio
async def test_read_missing_file_raises_file_not_found(impl, tmp_path):
    with pytest.raises(FileNotFoundError):
        await impl.read(str(tmp_path / "missing.txt"))


@pytest.mark.asyncio
async def test_read_directory_raises_file_not_found(impl, tmp_path):
    d = tmp_path / "somedir"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        await impl.read(str(d))


@pytest.mark.asyncio
async def test_read_round_trip_with_upload(impl, tmp_path):
    target = tmp_path / "rt.bin"
    content = b"\xde\xad\xbe\xef"
    await impl.upload(str(target), content)
    assert await impl.read(str(target)) == content


# ── remove ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_file_returns_file_path_type(impl, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("ok")
    result = await impl.remove(str(f))
    assert result["path_type"] == "file"
    assert result["target_path"] == str(f)
    assert not f.exists()


@pytest.mark.asyncio
async def test_remove_directory_returns_directory_path_type(impl, tmp_path):
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "inner.txt").write_text("content")
    result = await impl.remove(str(d))
    assert result["path_type"] == "directory"
    assert not d.exists()


@pytest.mark.asyncio
async def test_remove_missing_raises_file_not_found(impl, tmp_path):
    with pytest.raises(FileNotFoundError):
        await impl.remove(str(tmp_path / "nope"))


@pytest.mark.asyncio
async def test_remove_empty_path_raises_value_error(impl):
    with pytest.raises(ValueError):
        await impl.remove("")


# ── rmtree ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rmtree_removes_directory_and_returns_path(impl, tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "x.txt").write_text("y")
    (d / "sub").mkdir()
    out = await impl.rmtree(str(d))
    assert out == str(d)
    assert not d.exists()


@pytest.mark.asyncio
async def test_rmtree_file_unlinks_only(impl, tmp_path):
    """单文件也接 — backend Protocol delete_tree 是误导命名,调用方把单文件 path
    也丢进来 (TC-RSRC-C001/A009 走这条路径)。原行为 raise NotADirectoryError →
    backend 500 → HTTP 层 404 "File not found"。"""
    f = tmp_path / "f.txt"
    f.write_text("x")

    out = await impl.rmtree(str(f))

    assert out == str(f)
    assert not f.exists(), "单文件应被 unlink"


@pytest.mark.asyncio
async def test_rmtree_missing_raises_file_not_found(impl, tmp_path):
    with pytest.raises(FileNotFoundError):
        await impl.rmtree(str(tmp_path / "nonexistent"))


@pytest.mark.asyncio
async def test_rmtree_empty_path_raises_value_error(impl):
    with pytest.raises(ValueError):
        await impl.rmtree("")


@pytest.mark.asyncio
async def test_rmtree_symlink_to_dir_unlinks_only_keeps_target(impl, tmp_path):
    """symlink → unlink (只删链不删源)。singlebox 移除 skill 软链 (TC-CAP-C016)
    走这条路径。原行为: shutil.rmtree(symlink) raise OSError → 500。"""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "keep_me.txt").write_text("untouched")

    link = tmp_path / "link_to_source"
    link.symlink_to(source_dir, target_is_directory=True)

    out = await impl.rmtree(str(link))

    assert out == str(link)
    assert not link.exists() and not link.is_symlink(), "链应被 unlink"
    assert source_dir.exists(), "源目录必须保留"
    assert (source_dir / "keep_me.txt").read_text() == "untouched"


@pytest.mark.asyncio
async def test_rmtree_symlink_dangling_unlinks(impl, tmp_path):
    """指向不存在目标的悬空 symlink: unlink 也要成功 (Path.exists()=False
    所以不会进 is_symlink 分支... 实际会进 FileNotFoundError 分支)。
    本测试锁定行为:悬空链应当报 FileNotFoundError,与 path 不存在等价。"""
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "nonexistent_target", target_is_directory=True)

    # Path.exists() follows symlink → False for dangling. 走原 not-exists 分支
    with pytest.raises(FileNotFoundError):
        await impl.rmtree(str(link))


# ── list_dir ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_dir_flat_returns_entries(impl, tmp_path):
    (tmp_path / "a.txt").write_text("1")
    (tmp_path / "b").mkdir()
    result = await impl.list_dir(str(tmp_path))
    assert result["dir_path"] == str(tmp_path)
    assert result["recursive"] is False
    names = sorted(e["name"] for e in result["files"])
    assert names == ["a.txt", "b"]


@pytest.mark.asyncio
async def test_list_dir_flat_entry_fields(impl, tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("hello")
    result = await impl.list_dir(str(tmp_path))
    entry = next(e for e in result["files"] if e["name"] == "file.txt")
    assert entry["is_dir"] is False
    assert entry["size"] == 5
    assert entry["path"] == str(f)
    assert entry["relative_path"] == "file.txt"


@pytest.mark.asyncio
async def test_list_dir_directory_entry_fields(impl, tmp_path):
    d = tmp_path / "mydir"
    d.mkdir()
    result = await impl.list_dir(str(tmp_path))
    entry = next(e for e in result["files"] if e["name"] == "mydir")
    assert entry["is_dir"] is True
    assert entry["size"] == 0


@pytest.mark.asyncio
async def test_list_dir_recursive_includes_nested(impl, tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "deep.txt").write_text("zz")
    result = await impl.list_dir(str(tmp_path), recursive=True)
    assert result["recursive"] is True
    rels = {e["relative_path"] for e in result["files"]}
    assert "sub" in rels
    assert "sub/deep.txt" in rels


@pytest.mark.asyncio
async def test_list_dir_recursive_deep_nesting(impl, tmp_path):
    a = tmp_path / "a" / "b" / "c"
    a.mkdir(parents=True)
    (a / "leaf.txt").write_text("x")
    result = await impl.list_dir(str(tmp_path), recursive=True)
    rels = {e["relative_path"] for e in result["files"]}
    assert "a/b/c/leaf.txt" in rels


@pytest.mark.asyncio
async def test_list_dir_missing_raises_file_not_found(impl, tmp_path):
    with pytest.raises(FileNotFoundError):
        await impl.list_dir(str(tmp_path / "nope"))


@pytest.mark.asyncio
async def test_list_dir_file_raises_not_a_directory(impl, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("z")
    with pytest.raises(NotADirectoryError):
        await impl.list_dir(str(f))


@pytest.mark.asyncio
async def test_list_dir_empty_path_raises_value_error(impl):
    with pytest.raises(ValueError):
        await impl.list_dir("")


# ── all-empty-path validations in one shot ────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_path_validations(impl):
    with pytest.raises(ValueError):
        await impl.upload("", b"x")
    with pytest.raises(ValueError):
        await impl.remove("")
    with pytest.raises(ValueError):
        await impl.rmtree("")
    with pytest.raises(ValueError):
        await impl.list_dir("")
