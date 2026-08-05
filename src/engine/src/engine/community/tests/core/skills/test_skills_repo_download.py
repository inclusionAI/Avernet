"""Unit tests for engine.community.core.skills.skills_repo_download.

Covers every public and private function except the long-running background
thread loop.  All filesystem and network I/O is mocked so tests run fast
and deterministically.
"""

from __future__ import annotations

import importlib
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers – reload the module with patched env so module-level constants
# (TARGET_DIR, BACKUP_DIR, …) point at tmp dirs, not the real workspace.
# ---------------------------------------------------------------------------

_MOD_NAME = "engine.community.core.skills.skills_repo_download"


def _reload_module(tmp_root: Path):
    """Force-reload the module so that ``workspace_root()`` is re-evaluated
    against the current ``OPENCLAW_WORKSPACE_DIR`` env var pointing at *tmp_root*.
    """
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": str(tmp_root)}, clear=False):
        if _MOD_NAME in sys.modules:
            mod = importlib.reload(sys.modules[_MOD_NAME])
        else:
            mod = importlib.import_module(_MOD_NAME)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_root(tmp_path: Path):
    """Isolated workspace root so module-level constants point under tmp."""
    return tmp_path


@pytest.fixture()
def mod(tmp_root: Path):
    """Freshly-reloaded module with constants scoped to *tmp_root*."""
    return _reload_module(tmp_root)


@pytest.fixture()
def target_dir(mod, tmp_root: Path) -> Path:
    return tmp_root / "skills-pool" / "skills-repo"


@pytest.fixture()
def backup_dir(mod, tmp_root: Path) -> Path:
    return tmp_root / "skills-pool" / ".skills-repo-backups"


@pytest.fixture()
def etag_file(mod, tmp_root: Path) -> Path:
    return tmp_root / "skills-pool" / ".skills-repo-etag"


def _make_tar(root: Path, name: str = "skills-repo") -> Path:
    """Create a minimal tar.gz under *root* and return its path."""
    content_dir = root / name
    content_dir.mkdir()
    (content_dir / "hello.txt").write_text("world")
    tar_path = root / f"{name}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(str(content_dir), arcname=name)
    return tar_path


def _make_flat_tar(root: Path) -> Path:
    """Create a tar.gz whose entries sit at the archive root (no wrapper dir)."""
    (root / "flat_file.txt").write_text("flat")
    tar_path = root / "flat.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(str(root / "flat_file.txt"), arcname="flat_file.txt")
    return tar_path


# ===================================================================
# _is_agentbox_env
# ===================================================================


class TestIsAgentboxEnv:
    def test_true_when_mac_container_true(self, mod):
        with patch.dict(os.environ, {"MAC_CONTAINER": "true"}):
            assert mod._is_agentbox_env() is True

    def test_true_when_mac_container_1(self, mod):
        with patch.dict(os.environ, {"MAC_CONTAINER": "1"}):
            assert mod._is_agentbox_env() is True

    def test_true_when_uppercase(self, mod):
        with patch.dict(os.environ, {"MAC_CONTAINER": "True"}):
            assert mod._is_agentbox_env() is True

    def test_false_when_empty(self, mod):
        with patch.dict(os.environ, {"MAC_CONTAINER": ""}):
            assert mod._is_agentbox_env() is False

    def test_false_when_unset(self, mod):
        env = {k: v for k, v in os.environ.items() if k != "MAC_CONTAINER"}
        with patch.dict(os.environ, env, clear=True):
            assert mod._is_agentbox_env() is False

    def test_false_when_other_value(self, mod):
        with patch.dict(os.environ, {"MAC_CONTAINER": "false"}):
            assert mod._is_agentbox_env() is False


# ===================================================================
# _get_default_meta_url / _get_meta_url
# ===================================================================


class TestGetMetaUrl:
    def test_default_no_env_returns_empty(self, mod):
        env = {
            k: v
            for k, v in os.environ.items()
            if k
            not in (
                "AGENTCLAW_ENV",
                "SKILLS_REPO_META_URL",
                "SKILLS_REPO_META_URL_TEMPLATE",
            )
        }
        with patch.dict(os.environ, env, clear=True):
            with patch.object(
                mod, "_get_internal_default_meta_url_template", return_value=""
            ):
                assert mod._get_default_meta_url() == ""

    def test_meta_url_env_overrides_template(self, mod):
        with patch.dict(
            os.environ,
            {
                "SKILLS_REPO_META_URL": " https://custom/meta.json ",
                "SKILLS_REPO_META_URL_TEMPLATE": "https://template/meta-{env}.json",
                "AGENTCLAW_ENV": "pre",
            },
            clear=True,
        ):
            assert mod._get_meta_url() == "https://custom/meta.json"

    def test_meta_url_template_uses_agentclaw_env(self, mod):
        with patch.dict(
            os.environ,
            {
                "SKILLS_REPO_META_URL_TEMPLATE": "https://example.com/skills-repo-meta-{env}.json",
                "AGENTCLAW_ENV": "PRE",
            },
            clear=True,
        ):
            assert (
                mod._get_meta_url() == "https://example.com/skills-repo-meta-pre.json"
            )

    def test_meta_url_template_defaults_env_to_dev(self, mod):
        with patch.dict(
            os.environ,
            {
                "SKILLS_REPO_META_URL_TEMPLATE": "https://example.com/skills-repo-meta-{env}.json",
            },
            clear=True,
        ):
            assert (
                mod._get_meta_url() == "https://example.com/skills-repo-meta-dev.json"
            )

    def test_invalid_meta_url_template_returns_empty(self, mod):
        with patch.dict(
            os.environ,
            {
                "SKILLS_REPO_META_URL_TEMPLATE": "https://example.com/{missing}.json",
                "AGENTCLAW_ENV": "pre",
            },
            clear=True,
        ):
            assert mod._get_meta_url() == ""

    def test_internal_default_template_is_fallback(self, mod):
        with patch.dict(os.environ, {"AGENTCLAW_ENV": "PRE"}, clear=True):
            with patch.object(
                mod,
                "_get_internal_default_meta_url_template",
                return_value="https://example.com/internal-skills-repo-meta-{env}.json",
            ):
                assert (
                    mod._get_meta_url()
                    == "https://example.com/internal-skills-repo-meta-pre.json"
                )

    def test_meta_url_env_absent_returns_empty(self, mod):
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("SKILLS_REPO_META_URL", "SKILLS_REPO_META_URL_TEMPLATE")
        }
        with patch.dict(os.environ, env, clear=True):
            with patch.object(
                mod, "_get_internal_default_meta_url_template", return_value=""
            ):
                assert mod._get_meta_url() == ""


# ===================================================================
# _cleanup_stale_extract_dirs
# ===================================================================


class TestCleanupStaleExtractDirs:
    def test_removes_stale_dirs(self, mod, tmp_root: Path):
        parent = tmp_root / "skills-pool"
        parent.mkdir(parents=True)
        stale = parent / ".skills-repo-extract-abc123"
        stale.mkdir()
        (stale / "inner.txt").write_text("data")
        mod._cleanup_stale_extract_dirs()
        assert not stale.exists()

    def test_keeps_non_prefixed_dirs(self, mod, tmp_root: Path):
        parent = tmp_root / "skills-pool"
        parent.mkdir(parents=True)
        keep = parent / "skills-repo"
        keep.mkdir()
        mod._cleanup_stale_extract_dirs()
        assert keep.exists()

    def test_noop_when_parent_missing(self, mod, tmp_root: Path):
        # TARGET_DIR.parent does not exist → should not raise
        mod._cleanup_stale_extract_dirs()

    def test_handles_rmtree_failure(self, mod, tmp_root: Path):
        parent = tmp_root / "skills-pool"
        parent.mkdir(parents=True)
        stale = parent / ".skills-repo-extract-bad"
        stale.mkdir()
        with patch(
            "engine.community.core.skills.skills_repo_download.shutil.rmtree",
            side_effect=OSError("boom"),
        ):
            mod._cleanup_stale_extract_dirs()  # should log warning, not raise
        assert stale.exists()  # not removed


# ===================================================================
# _cleanup_old_backups
# ===================================================================


class TestCleanupOldBackups:
    def test_removes_expired_backups(self, mod, backup_dir: Path):
        backup_dir.mkdir(parents=True)
        old = backup_dir / "skills-repo-1000000000"
        old.mkdir()
        # mtime is recent at creation; force it old
        import os as _os

        _os.utime(str(old), (1000000000, 1000000000))
        mod._cleanup_old_backups()
        assert not old.exists()

    def test_keeps_fresh_backups(self, mod, backup_dir: Path):
        backup_dir.mkdir(parents=True)
        fresh = backup_dir / "skills-repo-9999999999"
        fresh.mkdir()
        mod._cleanup_old_backups()
        assert fresh.exists()

    def test_removes_excess_backups(self, mod, backup_dir: Path):
        backup_dir.mkdir(parents=True)
        b1 = backup_dir / "skills-repo-1000"
        b2 = backup_dir / "skills-repo-2000"
        b1.mkdir()
        b2.mkdir()
        # Make them "recent" so they aren't expired by age, but give b1 a
        # strictly older mtime than b2. _cleanup_old_backups sorts on
        # st_mtime to pick which to drop; equal mtimes leave the choice to
        # iterdir() order (filesystem-dependent) and make this test flaky.
        now = time.time()
        import os as _os

        _os.utime(str(b1), (now - 100, now - 100))
        _os.utime(str(b2), (now, now))
        mod._cleanup_old_backups()
        # MAX_BACKUP_COUNT=1: the older one should be removed
        remaining = list(backup_dir.iterdir())
        assert len(remaining) == 1
        assert remaining[0].name == "skills-repo-2000"

    def test_noop_when_backup_dir_missing(self, mod):
        mod._cleanup_old_backups()  # should not raise


# ===================================================================
# _backup_existing_repo
# ===================================================================


class TestBackupExistingRepo:
    def test_no_backup_when_target_missing(self, mod, target_dir: Path):
        assert mod._backup_existing_repo() is None

    def test_no_backup_when_target_empty(self, mod, target_dir: Path):
        target_dir.mkdir(parents=True)
        assert mod._backup_existing_repo() is None

    def test_backs_up_to_timestamped_dir(self, mod, target_dir: Path, backup_dir: Path):
        target_dir.mkdir(parents=True)
        (target_dir / "file.txt").write_text("content")
        with patch(
            "engine.community.core.skills.skills_repo_download.int", return_value=12345
        ):
            result = mod._backup_existing_repo()
        assert result is not None
        assert result.name == "skills-repo-12345"
        assert result.exists()
        assert (result / "file.txt").read_text() == "content"
        assert not target_dir.exists()

    def test_handles_timestamp_conflict(self, mod, target_dir: Path, backup_dir: Path):
        backup_dir.mkdir(parents=True)
        (backup_dir / "skills-repo-12345").mkdir()
        target_dir.mkdir(parents=True)
        (target_dir / "file.txt").write_text("content")
        with patch(
            "engine.community.core.skills.skills_repo_download.int", return_value=12345
        ):
            result = mod._backup_existing_repo()
        assert result is not None
        assert result.name == "skills-repo-12345-1"
        assert result.exists()

    def test_moves_entire_tree(self, mod, target_dir: Path, backup_dir: Path):
        target_dir.mkdir(parents=True)
        (target_dir / "a").mkdir()
        (target_dir / "a" / "b.txt").write_text("nested")
        with patch(
            "engine.community.core.skills.skills_repo_download.int", return_value=99
        ):
            result = mod._backup_existing_repo()
        assert (result / "a" / "b.txt").read_text() == "nested"


# ===================================================================
# _extract_atomic
# ===================================================================


class TestExtractAtomic:
    def test_extracts_and_swaps(self, mod, target_dir: Path, tmp_root: Path):
        tar_path = _make_tar(tmp_root, "skills-repo")
        ok = mod._extract_atomic(tar_path)
        assert ok is True
        assert target_dir.exists()
        assert (target_dir / "hello.txt").read_text() == "world"

    def test_flat_tar_swaps(self, mod, target_dir: Path, tmp_root: Path):
        tar_path = _make_flat_tar(tmp_root)
        ok = mod._extract_atomic(tar_path)
        assert ok is True
        assert target_dir.exists()
        assert (target_dir / "flat_file.txt").read_text() == "flat"

    def test_invalid_tar_returns_false(self, mod, tmp_root: Path):
        bad_tar = tmp_root / "bad.tar.gz"
        bad_tar.write_bytes(b"not a tar at all!!")
        ok = mod._extract_atomic(bad_tar)
        assert ok is False

    def test_cleans_up_tmp_dir_on_success(self, mod, tmp_root: Path):
        tar_path = _make_tar(tmp_root, "skills-repo")
        mod._extract_atomic(tar_path)
        parent = tmp_root / "skills-pool"
        leftover = [
            d
            for d in parent.iterdir()
            if d.is_dir() and d.name.startswith(".skills-repo-extract-")
        ]
        assert leftover == []

    def test_cleans_up_tmp_dir_on_failure(self, mod, tmp_root: Path):
        bad_tar = tmp_root / "bad.tar.gz"
        bad_tar.write_bytes(b"garbage")
        mod._extract_atomic(bad_tar)
        parent = tmp_root / "skills-pool"
        leftover = [
            d
            for d in parent.iterdir()
            if d.is_dir() and d.name.startswith(".skills-repo-extract-")
        ]
        assert leftover == []

    def test_backs_up_existing_before_swap(self, mod, target_dir: Path, tmp_root: Path):
        target_dir.mkdir(parents=True)
        (target_dir / "old.txt").write_text("old")
        tar_path = _make_tar(tmp_root, "skills-repo")
        with patch(
            "engine.community.core.skills.skills_repo_download.int", return_value=42
        ):
            mod._extract_atomic(tar_path)
        # new content should be in place
        assert (target_dir / "hello.txt").read_text() == "world"
        # old content should be in backup
        backup_dir = tmp_root / "skills-pool" / ".skills-repo-backups"
        backups = list(backup_dir.iterdir())
        assert len(backups) == 1
        assert (backups[0] / "old.txt").read_text() == "old"

    def test_replaces_existing_empty_repo(self, mod, target_dir: Path, tmp_root: Path):
        """An empty canonical directory has no state that requires a backup."""
        target_dir.mkdir(parents=True)
        tar_path = _make_tar(tmp_root, "skills-repo")

        ok = mod._extract_atomic(tar_path)

        assert ok is True
        assert (target_dir / "hello.txt").read_text() == "world"

    def test_preserves_target_if_backup_failed(
        self, mod, target_dir: Path, tmp_root: Path
    ):
        """A failed backup must not destroy the last usable repo."""
        target_dir.mkdir(parents=True)
        (target_dir / "stale.txt").write_text("stale")
        tar_path = _make_tar(tmp_root, "skills-repo")
        with patch(
            "engine.community.core.skills.skills_repo_download._backup_existing_repo",
            return_value=None,
        ):
            ok = mod._extract_atomic(tar_path)
        assert ok is False
        assert (target_dir / "stale.txt").read_text() == "stale"
        assert not (target_dir / "hello.txt").exists()

    def test_swap_failure_restores_previous_repo(
        self, mod, target_dir: Path, tmp_root: Path
    ):
        target_dir.mkdir(parents=True)
        (target_dir / "old.txt").write_text("old")
        tar_path = _make_tar(tmp_root, "skills-repo")
        real_move = mod.shutil.move

        def fail_new_publish(source: str, target: str):
            if Path(target) == target_dir:
                raise OSError("injected repo publish failure")
            return real_move(source, target)

        with patch.object(mod.shutil, "move", side_effect=fail_new_publish):
            ok = mod._extract_atomic(tar_path)

        assert ok is False
        assert (target_dir / "old.txt").read_text() == "old"
        assert not (target_dir / "hello.txt").exists()


# ===================================================================
# download_and_extract
# ===================================================================


class TestDownloadAndExtract:
    def test_success(self, mod, target_dir: Path, tmp_root: Path):
        tar_path = _make_tar(tmp_root, "skills-repo")
        tar_bytes = tar_path.read_bytes()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[tar_bytes])

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.get",
            return_value=mock_resp,
        ):
            ok = mod.download_and_extract("https://example.com/repo.tar.gz")
        assert ok is True
        assert (target_dir / "hello.txt").read_text() == "world"

    def test_retries_on_network_error_then_succeeds(
        self, mod, target_dir: Path, tmp_root: Path
    ):
        import requests as _req

        tar_path = _make_tar(tmp_root, "skills-repo")
        tar_bytes = tar_path.read_bytes()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[tar_bytes])

        call_count = 0

        def _flaky_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _req.RequestException("connection reset")
            return mock_resp

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.get",
            side_effect=_flaky_get,
        ):
            with patch("engine.community.core.skills.skills_repo_download.time.sleep"):
                ok = mod.download_and_extract("https://example.com/repo.tar.gz")
        assert ok is True
        assert call_count == 2

    def test_returns_false_after_max_retries(self, mod):
        import requests as _req

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.get",
            side_effect=_req.RequestException("down"),
        ):
            with patch("engine.community.core.skills.skills_repo_download.time.sleep"):
                ok = mod.download_and_extract("https://example.com/repo.tar.gz")
        assert ok is False

    def test_cleans_up_temp_file(self, mod, tmp_root: Path):
        """The temporary .tar.gz download file should be removed after
        both success and failure."""
        tar_path = _make_tar(tmp_root, "skills-repo")
        tar_bytes = tar_path.read_bytes()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[tar_bytes])

        created_files: list[Path] = []

        # Track what NamedTemporaryFile creates
        orig_ntf = tempfile.NamedTemporaryFile

        def _tracking_ntf(*args, **kwargs):
            f = orig_ntf(*args, **kwargs)
            created_files.append(Path(f.name))
            return f

        with patch(
            "engine.community.core.skills.skills_repo_download.tempfile.NamedTemporaryFile",
            _tracking_ntf,
        ):
            with patch(
                "engine.community.core.skills.skills_repo_download.requests.get",
                return_value=mock_resp,
            ):
                mod.download_and_extract("https://example.com/repo.tar.gz")

        for p in created_files:
            assert not p.exists(), f"temp file should be cleaned up: {p}"


# ===================================================================
# ETag helpers
# ===================================================================


class TestEtagHelpers:
    def test_load_last_etag_missing(self, mod, etag_file: Path):
        assert mod._load_last_etag() is None

    def test_load_last_etag_present(self, mod, etag_file: Path):
        etag_file.parent.mkdir(parents=True, exist_ok=True)
        etag_file.write_text('"abc123"')
        assert mod._load_last_etag() == '"abc123"'

    def test_load_last_etag_empty_file(self, mod, etag_file: Path):
        etag_file.parent.mkdir(parents=True, exist_ok=True)
        etag_file.write_text("  ")
        assert mod._load_last_etag() is None

    def test_save_last_etag(self, mod, etag_file: Path):
        mod._save_last_etag('"xyz"')
        assert etag_file.read_text() == '"xyz"'

    def test_save_last_etag_creates_parent(self, mod, etag_file: Path):
        assert not etag_file.parent.exists()
        mod._save_last_etag('"new"')
        assert etag_file.exists()


# ===================================================================
# _should_download
# ===================================================================


class TestShouldDownload:
    def test_true_when_target_missing(self, mod, target_dir: Path):
        assert mod._should_download("https://x.com/tar", '"etag"') is True

    def test_true_when_no_remote_etag(self, mod, target_dir: Path):
        target_dir.mkdir(parents=True)
        assert mod._should_download("https://x.com/tar", None) is True

    def test_true_when_no_local_etag(self, mod, target_dir: Path, etag_file: Path):
        target_dir.mkdir(parents=True)
        # etag_file doesn't exist yet
        assert mod._should_download("https://x.com/tar", '"remote"') is True

    def test_false_when_etags_match_locally(
        self, mod, target_dir: Path, etag_file: Path
    ):
        target_dir.mkdir(parents=True)
        etag_file.parent.mkdir(parents=True, exist_ok=True)
        etag_file.write_text('"same"')
        assert mod._should_download("https://x.com/tar", '"same"') is False

    def test_true_when_etags_differ_and_head_200(
        self, mod, target_dir: Path, etag_file: Path
    ):
        target_dir.mkdir(parents=True)
        etag_file.parent.mkdir(parents=True, exist_ok=True)
        etag_file.write_text('"local"')

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        with patch(
            "engine.community.core.skills.skills_repo_download.requests.head",
            return_value=mock_resp,
        ):
            result = mod._should_download("https://x.com/tar", '"remote"')
        assert result is True

    def test_false_when_head_returns_304(self, mod, target_dir: Path, etag_file: Path):
        target_dir.mkdir(parents=True)
        etag_file.parent.mkdir(parents=True, exist_ok=True)
        etag_file.write_text('"local"')

        mock_resp = MagicMock()
        mock_resp.status_code = 304
        with patch(
            "engine.community.core.skills.skills_repo_download.requests.head",
            return_value=mock_resp,
        ):
            result = mod._should_download("https://x.com/tar", '"remote"')
        assert result is False

    def test_true_when_head_fails_fallback_to_download(
        self, mod, target_dir: Path, etag_file: Path
    ):
        import requests as _req

        target_dir.mkdir(parents=True)
        etag_file.parent.mkdir(parents=True, exist_ok=True)
        etag_file.write_text('"local"')

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.head",
            side_effect=_req.RequestException("timeout"),
        ):
            result = mod._should_download("https://x.com/tar", '"remote"')
        assert result is True

    def test_head_sends_if_none_match(self, mod, target_dir: Path, etag_file: Path):
        """When etags differ locally, HEAD request should include If-None-Match."""
        target_dir.mkdir(parents=True)
        etag_file.parent.mkdir(parents=True, exist_ok=True)
        etag_file.write_text('"local-etag"')

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.head",
            return_value=mock_resp,
        ) as mock_head:
            mod._should_download("https://x.com/tar", '"remote-etag"')
            mock_head.assert_called_once_with(
                "https://x.com/tar",
                timeout=30,
                headers={"If-None-Match": '"local-etag"'},
            )


# ===================================================================
# _fetch_meta_info / _get_download_info
# ===================================================================


class TestFetchMetaInfo:
    def test_returns_none_when_meta_url_not_configured(self, mod):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(
                mod, "_get_internal_default_meta_url_template", return_value=""
            ):
                with patch(
                    "engine.community.core.skills.skills_repo_download.requests.get"
                ) as mock_get:
                    result = mod._fetch_meta_info()
        assert result is None
        mock_get.assert_not_called()

    def test_success(self, mod):
        payload = {
            "url": "https://oss.example.com/repo.tar.gz?sign=abc",
            "etag": '"v1"',
            "available": True,
            "oss_path": "skills-repo/skills-repo.tar.gz",
        }
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=payload)

        with patch.dict(
            os.environ,
            {"SKILLS_REPO_META_URL": "https://meta.example.com/meta.json"},
            clear=True,
        ):
            with patch(
                "engine.community.core.skills.skills_repo_download.requests.get",
                return_value=mock_resp,
            ):
                result = mod._fetch_meta_info()
        assert result == payload

    def test_returns_none_on_network_error(self, mod):
        import requests as _req

        with patch.dict(
            os.environ,
            {"SKILLS_REPO_META_URL": "https://meta.example.com/meta.json"},
            clear=True,
        ):
            with patch(
                "engine.community.core.skills.skills_repo_download.requests.get",
                side_effect=_req.RequestException("timeout"),
            ):
                result = mod._fetch_meta_info()
        assert result is None

    def test_returns_none_on_bad_json(self, mod):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(side_effect=ValueError("bad json"))

        with patch.dict(
            os.environ,
            {"SKILLS_REPO_META_URL": "https://meta.example.com/meta.json"},
            clear=True,
        ):
            with patch(
                "engine.community.core.skills.skills_repo_download.requests.get",
                return_value=mock_resp,
            ):
                result = mod._fetch_meta_info()
        assert result is None

    def test_returns_none_on_http_error(self, mod):
        import requests as _req

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock(side_effect=_req.HTTPError("404"))

        with patch.dict(
            os.environ,
            {"SKILLS_REPO_META_URL": "https://meta.example.com/meta.json"},
            clear=True,
        ):
            with patch(
                "engine.community.core.skills.skills_repo_download.requests.get",
                return_value=mock_resp,
            ):
                result = mod._fetch_meta_info()
        assert result is None


class TestGetDownloadInfo:
    def test_returns_url_and_etag(self, mod):
        meta = {
            "url": "https://oss.example.com/repo.tar.gz",
            "etag": '"v2"',
            "available": True,
            "oss_path": "x/y",
        }
        with patch.object(mod, "_fetch_meta_info", return_value=meta):
            url, etag = mod._get_download_info()
        assert url == "https://oss.example.com/repo.tar.gz"
        assert etag == '"v2"'

    def test_returns_none_when_not_available(self, mod):
        meta = {"url": "https://oss.example.com/repo.tar.gz", "available": False}
        with patch.object(mod, "_fetch_meta_info", return_value=meta):
            url, etag = mod._get_download_info()
        assert url is None
        assert etag is None

    def test_returns_none_when_url_missing(self, mod):
        meta = {"etag": '"v1"', "available": True}
        with patch.object(mod, "_fetch_meta_info", return_value=meta):
            url, etag = mod._get_download_info()
        assert url is None
        assert etag is None

    def test_returns_none_when_meta_is_none(self, mod):
        with patch.object(mod, "_fetch_meta_info", return_value=None):
            url, etag = mod._get_download_info()
        assert url is None
        assert etag is None

    def test_available_missing_treated_as_not_available(self, mod):
        """When 'available' key is absent (None), should treat as unavailable."""
        meta = {"url": "https://oss.example.com/repo.tar.gz", "etag": '"v1"'}
        with patch.object(mod, "_fetch_meta_info", return_value=meta):
            url, etag = mod._get_download_info()
        assert url is None
        assert etag is None


# ===================================================================
# prepare_pool_layout
# ===================================================================


class TestPreparePoolLayout:
    def test_noop_when_not_agentbox(self, mod, tmp_root: Path):
        with patch.object(mod, "_is_agentbox_env", return_value=False):
            with patch.object(mod, "prepare_desktop_pool") as mock_prepare:
                mod.prepare_pool_layout(home=tmp_root)

        mock_prepare.assert_not_called()

    def test_reuses_current_repo_for_configured_engine(
        self,
        mod,
        target_dir: Path,
        tmp_root: Path,
    ):
        target_dir.mkdir(parents=True)
        expected = SimpleNamespace(
            status=SimpleNamespace(value="PREPARED"),
            preparation_id="P1",
            reason=None,
        )
        with patch.object(mod, "_is_agentbox_env", return_value=True):
            with patch.object(
                mod,
                "load_engine_config",
                return_value=SimpleNamespace(default_engine="Hermes"),
            ):
                with patch.object(
                    mod,
                    "prepare_desktop_pool",
                    return_value=expected,
                ) as mock_prepare:
                    mod.prepare_pool_layout(home=tmp_root)

        mock_prepare.assert_called_once_with(
            engine="hermes",
            repo_source=target_dir,
            home=tmp_root,
        )

    def test_preparation_failure_is_non_fatal(
        self,
        mod,
        target_dir: Path,
        tmp_root: Path,
    ):
        target_dir.mkdir(parents=True)
        with patch.object(mod, "_is_agentbox_env", return_value=True):
            with patch.object(
                mod,
                "load_engine_config",
                side_effect=RuntimeError("broken config"),
            ):
                mod.prepare_pool_layout(home=tmp_root)


# ===================================================================
# _download_and_save
# ===================================================================


class TestDownloadAndSave:
    def test_saves_etag_on_success(
        self, mod, target_dir: Path, tmp_root: Path, etag_file: Path
    ):
        tar_path = _make_tar(tmp_root, "skills-repo")
        tar_bytes = tar_path.read_bytes()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[tar_bytes])

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.get",
            return_value=mock_resp,
        ):
            with patch.object(mod, "prepare_pool_layout") as mock_prepare:
                ok = mod._download_and_save(
                    "https://x.com/repo.tar.gz",
                    '"etag1"',
                )
        assert ok is True
        assert etag_file.read_text() == '"etag1"'
        mock_prepare.assert_called_once_with()

    def test_does_not_save_etag_on_failure(self, mod, etag_file: Path):
        import requests as _req

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.get",
            side_effect=_req.RequestException("down"),
        ):
            with patch("engine.community.core.skills.skills_repo_download.time.sleep"):
                ok = mod._download_and_save("https://x.com/repo.tar.gz", '"etag1"')
        assert ok is False
        assert not etag_file.exists()

    def test_ok_without_etag(
        self, mod, target_dir: Path, tmp_root: Path, etag_file: Path
    ):
        tar_path = _make_tar(tmp_root, "skills-repo")
        tar_bytes = tar_path.read_bytes()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[tar_bytes])

        with patch(
            "engine.community.core.skills.skills_repo_download.requests.get",
            return_value=mock_resp,
        ):
            ok = mod._download_and_save("https://x.com/repo.tar.gz", None)
        assert ok is True
        assert not etag_file.exists()  # no etag → no etag file


# ===================================================================
# bootstrap_on_startup
# ===================================================================


class TestBootstrapOnStartup:
    def test_cleans_up_even_in_non_agentbox(self, mod, tmp_root: Path):
        """Cleanup of stale dirs should run regardless of MAC_CONTAINER."""
        parent = tmp_root / "skills-pool"
        parent.mkdir(parents=True)
        stale = parent / ".skills-repo-extract-xyz"
        stale.mkdir()

        env = {k: v for k, v in os.environ.items() if k != "MAC_CONTAINER"}
        with patch.dict(os.environ, env, clear=True):
            mod._is_agentbox_env.cache_clear() if hasattr(
                mod._is_agentbox_env, "cache_clear"
            ) else None
            mod.bootstrap_on_startup()

        assert not stale.exists()

    def test_skips_download_when_not_agentbox(self, mod, target_dir: Path):
        env = {k: v for k, v in os.environ.items() if k != "MAC_CONTAINER"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(mod, "_get_download_info") as mock_dl:
                mod.bootstrap_on_startup()
                mock_dl.assert_not_called()

    def test_downloads_when_agentbox_and_url_available(
        self, mod, target_dir: Path, tmp_root: Path
    ):
        tar_path = _make_tar(tmp_root, "skills-repo")
        tar_bytes = tar_path.read_bytes()

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.iter_content = MagicMock(return_value=[tar_bytes])

        with patch.dict(os.environ, {"MAC_CONTAINER": "true"}):
            with patch.object(mod, "_is_agentbox_env", return_value=True):
                with patch.object(
                    mod,
                    "_get_download_info",
                    return_value=("https://x.com/tar", '"e1"'),
                ):
                    with patch.object(mod, "_should_download", return_value=True):
                        with patch(
                            "engine.community.core.skills.skills_repo_download.requests.get",
                            return_value=mock_resp,
                        ):
                            mod.bootstrap_on_startup()

        assert (target_dir / "hello.txt").read_text() == "world"

    def test_skips_download_when_should_download_false(self, mod):
        with patch.dict(os.environ, {"MAC_CONTAINER": "true"}):
            with patch.object(mod, "_is_agentbox_env", return_value=True):
                with patch.object(
                    mod,
                    "_get_download_info",
                    return_value=("https://x.com/tar", '"e1"'),
                ):
                    with patch.object(mod, "_should_download", return_value=False):
                        with patch.object(mod, "_download_and_save") as mock_ds:
                            with patch.object(
                                mod, "prepare_pool_layout"
                            ) as mock_prepare:
                                mod.bootstrap_on_startup()
                                mock_ds.assert_not_called()
                                mock_prepare.assert_called_once_with()

    def test_skips_download_when_no_url(self, mod):
        with patch.dict(os.environ, {"MAC_CONTAINER": "true"}):
            with patch.object(mod, "_is_agentbox_env", return_value=True):
                with patch.object(mod, "_get_download_info", return_value=(None, None)):
                    with patch.object(mod, "_download_and_save") as mock_ds:
                        with patch.object(mod, "prepare_pool_layout") as mock_prepare:
                            mod.bootstrap_on_startup()
                            mock_ds.assert_not_called()
                            mock_prepare.assert_called_once_with()


# ===================================================================
# _sync_once
# ===================================================================


class TestSyncOnce:
    def test_no_url_still_refreshes_preparation(self, mod):
        with patch.object(mod, "_get_download_info", return_value=(None, None)):
            with patch.object(mod, "prepare_pool_layout") as mock_prepare:
                mod._sync_once()

        mock_prepare.assert_called_once_with()

    def test_unchanged_repo_still_refreshes_preparation(self, mod):
        with patch.object(
            mod,
            "_get_download_info",
            return_value=("https://x.com/tar", '"e1"'),
        ):
            with patch.object(mod, "_should_download", return_value=False):
                with patch.object(mod, "_download_and_save") as mock_download:
                    with patch.object(mod, "prepare_pool_layout") as mock_prepare:
                        mod._sync_once()

        mock_download.assert_not_called()
        mock_prepare.assert_called_once_with()

    def test_download_success_prepares_through_download_helper(self, mod):
        with patch.object(
            mod,
            "_get_download_info",
            return_value=("https://x.com/tar", '"e1"'),
        ):
            with patch.object(mod, "_should_download", return_value=True):
                with patch.object(
                    mod,
                    "_download_and_save",
                    return_value=True,
                ) as mock_download:
                    mod._sync_once()

        mock_download.assert_called_once_with("https://x.com/tar", '"e1"')


# ===================================================================
# start_background_sync
# ===================================================================


class TestStartBackgroundSync:
    def test_noop_when_not_agentbox(self, mod):
        env = {k: v for k, v in os.environ.items() if k != "MAC_CONTAINER"}
        with patch.dict(os.environ, env, clear=True):
            with patch.object(mod, "_is_agentbox_env", return_value=False):
                mod.start_background_sync()
        # No thread should be started; just return.

    def test_starts_daemon_thread_when_agentbox(self, mod):
        with patch.object(mod, "_is_agentbox_env", return_value=True):
            # Patch the sync loop to exit immediately after one check
            call_count = 0

            def _fake_sync_loop():
                nonlocal call_count
                call_count += 1

            with patch.object(mod, "_get_download_info", return_value=(None, None)):
                with patch(
                    "engine.community.core.skills.skills_repo_download.threading.Thread"
                ) as MockThread:
                    mod.start_background_sync(interval_seconds=60)
                    # Verify Thread was created with daemon=True
                    MockThread.assert_called_once()
                    _, kwargs = MockThread.call_args
                    assert kwargs["daemon"] is True
                    MockThread.return_value.start.assert_called_once()
