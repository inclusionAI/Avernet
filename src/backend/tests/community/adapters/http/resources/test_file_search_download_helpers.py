"""Unit tests for the search/zip router's local helpers.

``_bind_id_from_publish`` (the published-stage binding lookup), the
``_resolve_walk_device_fs`` device-fs resolver, and ``_abs_path`` (the in-container
absolute-path builder for search/zip results) are exercised directly — the
endpoint happy-paths don't reach the publish / container branches.
"""
import os
import stat
import zipfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.adapters.http.resources.file_search_download_router import (
    _abs_path,
    _bind_id_from_publish,
    _device_fs_for_bot,
    _download_arcname,
    _download_logical,
    _resolve_walk_device_fs,
    _walk_device_fs,
    _zip_dir_entry,
    _zip_file_entry,
    download_directory,
)

pytestmark = pytest.mark.unit


def _repo_with_ext(ext, status="success"):
    repo = MagicMock()
    repo.get_by_id.return_value = SimpleNamespace(ext=ext, status=status)
    return repo


# ── _bind_id_from_publish ─────────────────────────────────────────────────────


class TestBindIdFromPublish:
    def test_online_stage_returns_bind_id(self):
        repo = _repo_with_ext({"binding": {"online": 7}})
        assert _bind_id_from_publish("1", repo) == 7

    def test_validating_status_uses_verify_binding(self):
        repo = _repo_with_ext({"binding": {"online": 7, "verify": 11}}, status="validating")
        assert _bind_id_from_publish("1", repo) == 11

    def test_record_not_found_returns_none(self):
        repo = MagicMock()
        repo.get_by_id.return_value = None
        assert _bind_id_from_publish("1", repo) is None

    def test_no_bind_id_returns_none(self):
        repo = _repo_with_ext({"binding": {}})
        assert _bind_id_from_publish("1", repo) is None

    def test_non_numeric_publish_id_returns_none(self):
        # int(publish_id) raises → swallowed → None
        assert _bind_id_from_publish("not-a-number", MagicMock()) is None


# ── _resolve_walk_device_fs ───────────────────────────────────────────────────


class TestResolveWalkDeviceFs:
    def test_publish_resolves_binding_then_dispatches(self):
        repo = _repo_with_ext({"binding": {"online": 7}})
        ctx = SimpleNamespace(provider="arca")
        resolver = MagicMock()
        resolver.resolve_for_binding.return_value = ctx
        dispatcher = MagicMock()
        dispatcher.dispatch_addressed.return_value = "published-fs"

        fs = _resolve_walk_device_fs(
            publish_id="1", bot_id="b1", owner_id="o1", operator_id="u1",
            engine_type="openclaw", publish_repo=repo, bot_repo=MagicMock(),
            resolver=resolver, dispatcher=dispatcher,
        )
        assert fs == "published-fs"
        resolver.resolve_for_binding.assert_called_once_with(
            7, "u1", bot_id="b1", device_uuid=None
        )
        assert dispatcher.dispatch_addressed.call_args.kwargs["namespace"] == "workspace"

    def test_publish_missing_binding_returns_none(self):
        repo = _repo_with_ext({"binding": {}})
        fs = _resolve_walk_device_fs(
            publish_id="1", bot_id="b1", owner_id="o1", operator_id="u1",
            engine_type="openclaw", publish_repo=repo, bot_repo=MagicMock(),
            resolver=MagicMock(), dispatcher=MagicMock(),
        )
        assert fs is None

    def test_arca_draft_uses_device_fs_for_bot(self):
        bot_repo = MagicMock()
        resolver = MagicMock()
        dispatcher = MagicMock()
        dispatcher.dispatch_addressed.return_value = "draft-fs"
        with patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info",
            return_value=("arca", "sbx"),
        ):
            fs = _resolve_walk_device_fs(
                publish_id=None, bot_id="b1", owner_id="o1", operator_id="u1",
                engine_type="openclaw", publish_repo=MagicMock(), bot_repo=bot_repo,
                resolver=resolver, dispatcher=dispatcher,
            )
        assert fs == "draft-fs"

    def test_local_provider_returns_none(self):
        with patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info",
            return_value=("local", None),
        ):
            fs = _resolve_walk_device_fs(
                publish_id=None, bot_id="b1", owner_id="o1", operator_id="u1",
                engine_type="openclaw", publish_repo=MagicMock(), bot_repo=MagicMock(),
                resolver=MagicMock(), dispatcher=MagicMock(),
            )
        assert fs is None


# ── _abs_path ─────────────────────────────────────────────────────────────────


class TestAbsPath:
    def test_arca_engine_uses_container_root(self):
        assert _abs_path("/nas/ws", "data/x.csv", "openclaw") == \
            "/home/admin/.openclaw/workspace/data/x.csv"

    def test_non_arca_engine_uses_host_dir(self):
        assert _abs_path("/nas/ws", "data/x.csv") == "/nas/ws/data/x.csv"

    def test_empty_rel_returns_base(self):
        assert _abs_path("/nas/ws", "") == "/nas/ws"
        assert _abs_path("/nas/ws", "", "openclaw") == "/home/admin/.openclaw/workspace"


# ── _walk_device_fs / _device_fs_for_bot ─────────────────────────────────────


def _entry(name, is_dir=False, size=None):
    return {"name": name, "is_dir": is_dir, "size": size,
            "size_human": None, "modified_at": None}


def _rel_of(logical: str) -> str:
    """``workspace`` → ``""``; ``workspace/<rel>`` → ``<rel>``."""
    if logical == "workspace":
        return ""
    if logical.startswith("workspace/"):
        return logical[len("workspace/"):]
    return logical


class _FakeDeviceFs:
    """Minimal device-fs stand-in: ``list_dir(logical)`` looks up a static tree.

    The tree maps a workspace-relative path → list of entries; dotfiles and the
    walk's own bookkeeping are exercised exactly as the real plugin returns.
    Raises ``KeyError`` for an unknown rel to surface a mis-seeded tree loudly.
    """

    def __init__(self, tree):
        self._tree = tree

    async def list_dir(self, logical):
        return self._tree[_rel_of(logical)]


class TestWalkDeviceFs:
    @pytest.mark.asyncio
    async def test_recursive_flatten_with_rel_paths(self):
        tree = {
            "": [_entry("data", is_dir=True), _entry("report.csv", size=10)],
            "data": [_entry("deep.json", size=2), _entry(".hidden", size=0)],
        }
        out = await _walk_device_fs(_FakeDeviceFs(tree), "", should_descend=lambda r: True)
        names = {e["name"] for e in out}
        rels = {e["rel"] for e in out}
        assert {"data", "report.csv", "deep.json"} <= names
        assert "data/deep.json" in rels and "report.csv" in rels
        # dotfiles are skipped
        assert ".hidden" not in names

    @pytest.mark.asyncio
    async def test_should_descend_prunes_subtree(self):
        tree = {
            "": [_entry("skills", is_dir=True), _entry("keep.txt", size=1)],
            "skills": [_entry("skills-local", is_dir=True), _entry("other", is_dir=True)],
            "skills/skills-local": [_entry("a.md", size=1)],
            "skills/other": [_entry("b.md", size=1)],
        }

        def descend(rel):
            # only the skills/skills-local subtree is walked
            return rel == "skills" or rel.startswith("skills/skills-local")

        out = await _walk_device_fs(_FakeDeviceFs(tree), "", should_descend=descend)
        rels = {e["rel"] for e in out}
        assert "skills/skills-local/a.md" in rels
        assert "skills/other/b.md" not in rels  # pruned before listing

    @pytest.mark.asyncio
    async def test_root_list_failure_returns_none(self):
        class FailRoot:
            async def list_dir(self, logical):
                raise RuntimeError("container unreachable")

        out = await _walk_device_fs(FailRoot(), "", should_descend=None)
        assert out is None

    @pytest.mark.asyncio
    async def test_subdir_failure_is_swallowed_not_root(self):
        tree = {
            "": [_entry("ok", is_dir=True), _entry("fine.txt", size=1)],
            "ok": [],  # listed without raising (subdir failure is simulated below)
        }

        class Partial(_FakeDeviceFs):
            async def list_dir(self, logical):
                if _rel_of(logical) == "ok":
                    raise RuntimeError("nested read fail")
                return await super().list_dir(logical)

        out = await _walk_device_fs(Partial(tree), "", should_descend=lambda r: True)
        # root listed ok → not None; the failing subdir just yields nothing extra
        assert out is not None
        assert {"ok", "fine.txt"} <= {e["name"] for e in out}

    @pytest.mark.asyncio
    async def test_max_entries_caps_walk(self):
        many = [_entry(f"f{i}.txt", size=1) for i in range(20)]
        out = await _walk_device_fs(
            _FakeDeviceFs({"": many}), "", should_descend=None, max_entries=5,
        )
        assert len(out) == 5

    @pytest.mark.asyncio
    async def test_max_entries_caps_after_descending(self):
        # dir is emitted first (out=1 < cap=1? no, 1>=1 stops before descending) →
        # use cap=2 so the dir descends, b1 hits the inner cap, then the post-recursion
        # guard (line after `await walk(rel)`) also returns.
        tree = {
            "": [_entry("dir", is_dir=True)],
            "dir": [_entry("b1", size=1), _entry("b2", size=1)],
        }
        out = await _walk_device_fs(
            _FakeDeviceFs(tree), "", should_descend=lambda r: True, max_entries=2,
        )
        assert len(out) == 2
        assert [e["name"] for e in out] == ["dir", "b1"]

    @pytest.mark.asyncio
    async def test_scoped_base_rel(self):
        tree = {
            "": [_entry("should_not_appear", size=1)],
            "sub": [_entry("in_scope.txt", size=1)],
        }
        out = await _walk_device_fs(
            _FakeDeviceFs(tree), "sub", should_descend=None,
        )
        # rel is workspace-relative (carries the base prefix), so the root-level
        # entry outside the scope is never listed.
        assert [e["rel"] for e in out] == ["sub/in_scope.txt"]


class TestDeviceFsForBot:
    def test_dispatches_addressed_with_resolved_ctx(self):
        ctx = SimpleNamespace(provider="baas")
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = ctx
        dispatcher = MagicMock()
        dispatcher.dispatch_addressed.return_value = "the-device-fs"

        fs = _device_fs_for_bot(
            "bot-1", "owner-1", "openclaw", resolver, dispatcher,
        )
        assert fs == "the-device-fs"
        resolver.resolve_for_bot.assert_called_once_with(
            "bot-1", "owner-1", device_uuid=None
        )
        dispatcher.dispatch_addressed.assert_called_once()
        _, kwargs = dispatcher.dispatch_addressed.call_args
        assert kwargs["namespace"] == "workspace"
        assert kwargs["entity_type"] == "staff"
        assert kwargs["entity_id"] == "owner-1"
        assert kwargs["bot_id"] == "bot-1"
        assert kwargs["engine_type"] == "openclaw"

    def test_propagates_resolver_failure(self):
        resolver = MagicMock()
        resolver.resolve_for_bot.side_effect = RuntimeError("no active binding")
        dispatcher = MagicMock()
        with pytest.raises(RuntimeError, match="no active binding"):
            _device_fs_for_bot("bot-1", "owner-1", "openclaw", resolver, dispatcher)
        dispatcher.dispatch_addressed.assert_not_called()


# ── _download_logical / _download_arcname — the double-join regression ────────


class TestDownloadArcHelpers:
    def test_logical_flat_joins_rel_onto_workspace(self):
        # rel already carries the path prefix (workspace-relative); joining
        # without re-stitching path is the fix.
        assert _download_logical("memory/foo.txt") == "workspace/memory/foo.txt"
        assert _download_logical("foo.txt") == "workspace/foo.txt"

    def test_arcname_strips_path_prefix(self):
        assert _download_arcname("memory/foo.txt", "memory") == "foo.txt"
        assert _download_arcname("memory/sub/bar.txt", "memory") == "sub/bar.txt"

    def test_arcname_root_keeps_rel(self):
        assert _download_arcname("foo.txt", "") == "foo.txt"
        assert _download_arcname("memory/foo.txt", "") == "memory/foo.txt"


# ── download_directory device-fs branch: subdir double-join regression ───────


class _RecordingDeviceFs:
    """Device-fs stand-in that records every ``read_file`` logical and serves
    bytes from a static ``{logical: payload}`` map. Unknown logicals return
    ``None`` (mirrors the plugin's not-found contract) — which is what made the
    double-joined path silently produce a header-only empty zip.
    """

    def __init__(self, list_tree, read_payload):
        self._list_tree = list_tree
        self._read_payload = read_payload
        self.read_logicals: list[str] = []

    async def list_dir(self, logical):
        return self._list_tree.get(_rel_of(logical))

    async def read_file(self, logical, enforce_download_limit=False):
        self.read_logicals.append(logical)
        return self._read_payload.get(logical)


def _mock_device_fs_dispatcher(fs):
    dispatcher = MagicMock()
    dispatcher.dispatch_addressed.return_value = fs
    return dispatcher


class TestDownloadDirectoryDeviceFsDoubleJoin:
    """Regression for the subdir ``download-dir`` empty-zip bug.

    ``_walk_device_fs`` returns ``rel`` already carrying the ``path`` prefix
    (workspace-relative). The read loop must join it onto the workspace
    namespace *without* re-stitching ``path`` — otherwise the read path doubles
    (``workspace/memory/memory/foo.txt``), the device returns ``None`` for every
    file, and the zip is header-only. These pin the flat-join behavior.
    """

    @pytest.mark.asyncio
    async def test_subdir_read_path_is_flat_join_no_double(self, tmp_path):
        list_tree = {
            "": [_entry("memory", is_dir=True)],
            "memory": [_entry("foo.txt", size=3), _entry("bar.txt", size=3)],
        }
        read_payload = {
            "workspace/memory/foo.txt": b"FOO",
            "workspace/memory/bar.txt": b"BAR",
        }
        fs = _RecordingDeviceFs(list_tree, read_payload)
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = SimpleNamespace(provider="arca")

        with patch(
            "agentclaw.community.adapters.http.resources.file_router.resolve_engine_for_bot",
            return_value="openclaw",
        ), patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info",
            return_value=("arca", "sbx"),
        ), patch(
            "agentclaw.community.adapters.http.resources.file_search_download_router.get_bot_workspace_dir",
            return_value=tmp_path,
        ):
            resp = await download_directory.__wrapped__(
                path="memory", bot_id="b1", engine_type="openclaw", owner_id="u1",
                publish_id=None, ctx=SimpleNamespace(user_id="u1", bot_id="b1"),
                bot_repo=MagicMock(), path_factory=MagicMock(),
                publish_repo=MagicMock(), baas_service=MagicMock(),
                resolver=resolver, device_fs_dispatcher=_mock_device_fs_dispatcher(fs),
            )

        # read_file got the flat workspace path — never the doubled form.
        assert fs.read_logicals, "read_file was never called — walk yielded no files"
        assert all("memory/memory" not in lg for lg in fs.read_logicals), fs.read_logicals
        assert "workspace/memory/foo.txt" in fs.read_logicals
        assert "workspace/memory/bar.txt" in fs.read_logicals

        with zipfile.ZipFile(resp.path) as zf:
            names = zf.namelist()
            assert "memory/foo.txt" in names, names
            assert "memory/bar.txt" in names, names
            assert all("/memory/memory/" not in n for n in names), names
            assert zf.read("memory/foo.txt") == b"FOO"
        os.unlink(resp.path)

    @pytest.mark.asyncio
    async def test_root_download_does_not_regress(self, tmp_path):
        """``path`` empty (whole-workspace root) keeps the single-join it always
        had — the fix must not break the previously-working root case."""
        list_tree = {"": [_entry("readme.txt", size=4)]}
        read_payload = {"workspace/readme.txt": b"READ"}
        fs = _RecordingDeviceFs(list_tree, read_payload)
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = SimpleNamespace(provider="arca")

        with patch(
            "agentclaw.community.adapters.http.resources.file_router.resolve_engine_for_bot",
            return_value="openclaw",
        ), patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info",
            return_value=("arca", "sbx"),
        ), patch(
            "agentclaw.community.adapters.http.resources.file_search_download_router.get_bot_workspace_dir",
            return_value=tmp_path,
        ):
            resp = await download_directory.__wrapped__(
                path="", bot_id="b1", engine_type="openclaw", owner_id="u1",
                publish_id=None, ctx=SimpleNamespace(user_id="u1", bot_id="b1"),
                bot_repo=MagicMock(), path_factory=MagicMock(),
                publish_repo=MagicMock(), baas_service=MagicMock(),
                resolver=resolver, device_fs_dispatcher=_mock_device_fs_dispatcher(fs),
            )

        assert "workspace/readme.txt" in fs.read_logicals
        with zipfile.ZipFile(resp.path) as zf:
            names = zf.namelist()
            assert "workspace/readme.txt" in names, names
            assert zf.read("workspace/readme.txt") == b"READ"
        os.unlink(resp.path)


# ── _zip_file_entry / _zip_dir_entry — macOS Archive Utility compatibility ────


class TestZipEntryMetadata:
    """The zip must carry machine-readable file/dir type bits so macOS Archive
    Utility reads the entries (otherwise it reports "archive is empty" even
    though ``unzip``/Windows extract fine — those key off the trailing ``/``)."""

    def test_file_entry_has_s_ifreg_type_bits(self):
        zi = _zip_file_entry("a/file.txt")
        assert (zi.external_attr >> 16) & 0o170000 == stat.S_IFREG
        assert (zi.external_attr >> 16) & 0o777 == 0o644

    def test_dir_entry_has_trailing_slash_and_s_ifdir(self):
        zi = _zip_dir_entry("a")
        assert zi.filename.endswith("/")
        assert (zi.external_attr >> 16) & 0o170000 == stat.S_IFDIR
        assert (zi.external_attr >> 16) & 0o777 == 0o755

    def test_dir_entry_keeps_existing_trailing_slash(self):
        zi = _zip_dir_entry("a/sub/")
        assert zi.filename == "a/sub/"


# ── download_directory: zip produced is GUI-archive-tool compatible ────────────


class TestDownloadDirectoryZipMetadata:
    """The streamed zip must look like a platform-built one (dir entry + file
    entries with Unix type bits) so macOS Archive Utility double-click works."""

    @pytest.mark.asyncio
    async def test_zip_has_dir_entry_and_typed_file_entries(self, tmp_path):
        list_tree = {
            "": [_entry("memory", is_dir=True)],
            "memory": [_entry("foo.txt", size=3)],
        }
        read_payload = {"workspace/memory/foo.txt": b"FOO"}
        fs = _RecordingDeviceFs(list_tree, read_payload)
        resolver = MagicMock()
        resolver.resolve_for_bot.return_value = SimpleNamespace(provider="arca")

        with patch(
            "agentclaw.community.adapters.http.resources.file_router.resolve_engine_for_bot",
            return_value="openclaw",
        ), patch(
            "agentclaw.community.core.devices.services.device_info.get_device_info",
            return_value=("arca", "sbx"),
        ), patch(
            "agentclaw.community.adapters.http.resources.file_search_download_router.get_bot_workspace_dir",
            return_value=tmp_path,
        ):
            resp = await download_directory.__wrapped__(
                path="memory", bot_id="b1", engine_type="openclaw", owner_id="u1",
                publish_id=None, ctx=SimpleNamespace(user_id="u1", bot_id="b1"),
                bot_repo=MagicMock(), path_factory=MagicMock(),
                publish_repo=MagicMock(), baas_service=MagicMock(),
                resolver=resolver, device_fs_dispatcher=_mock_device_fs_dispatcher(fs),
            )

        with zipfile.ZipFile(resp.path) as zf:
            infos = {i.filename: i for i in zf.infolist()}
            # root folder entry present + marked as a directory
            assert "memory/" in infos, list(infos)
            assert infos["memory/"].is_dir()
            assert (infos["memory/"].external_attr >> 16) & 0o170000 == stat.S_IFDIR
            # file entry has the regular-file type bit (the macOS "empty archive" bug)
            assert "memory/foo.txt" in infos
            file_attr = infos["memory/foo.txt"].external_attr >> 16
            assert file_attr & 0o170000 == stat.S_IFREG, oct(file_attr)
            assert zf.read("memory/foo.txt") == b"FOO"
        os.unlink(resp.path)

