"""Integration test: GitSyncService.sync_bootstrap against a real local bare repo.

Unlike the unit tests in ``test_git_sync_bootstrap.py`` (which mock every
subprocess), this exercises the real clone path end to end:

    git clone --bare → git fetch → git archive | tar | rsync → skills land.

It uses the ``local_skills_bare_repo`` fixture (a seeded file:// bare repo) and
injects that URL through ``SecretResolver``. ``GIT_SYNC_TMP_BASE`` is redirected
so nothing touches the prod aiworkbench.git or the developer's ~/aiworkbench dir.

OSS is mocked: bootstrap calls ``_sync_upload_skills_repo_to_oss`` and
``_sync_refresh_meta_to_oss``, the latter raising unless ``get_etag`` is
non-None, so the stub returns a fixed etag.

Known limitation: relies on git/tar/rsync subprocesses. Reliable on CI (Linux);
may fail on macOS where openrsync/bsdtar differ. Skips entirely if git is absent.
"""
import asyncio
import shutil
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentclaw.community.core.skill_center.services.git_sync import (
    GitSyncConfig,
    GitSyncService,
)
from agentclaw.community.plugins.local.cache import MemoryCachePlugin
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin

pytestmark = pytest.mark.integration


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
@pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("tar") is None,
    reason="rsync/tar not available",
)
def test_bootstrap_clones_and_extracts_skills(
    local_skills_bare_repo, tmp_path, monkeypatch
):
    bare = local_skills_bare_repo
    monkeypatch.setenv("GIT_SYNC_TMP_BASE", str(tmp_path / "git-tmp"))

    # GitSyncConfig defaults paths under ~/aiworkbench; redirect to tmp so the
    # test is hermetic and the clone/extract actually happens (no pre-existing repo).
    # NB: clone destination must differ from the fixture's bare repo
    # (also at tmp_path/aiworkbench.git) or bootstrap short-circuits as "existing".
    config = GitSyncConfig()
    config.local_bare_repo = tmp_path / "clone" / "aiworkbench.git"
    config.skills_target = tmp_path / "skills-repo"
    config.subtrees[0]["target_dir"] = config.skills_target

    # OSS stub: bootstrap uploads tar.gz then refreshes meta JSON. Meta refresh
    # raises if get_etag returns None, so hand it a non-None etag.
    oss = MagicMock(spec=ObjectStoragePlugin)
    oss.put_file.return_value = True
    oss.put_object.return_value = True
    oss.set_object_acl.return_value = True
    oss.sign_url.return_value = "mock://x"
    oss.get_etag.return_value = "etag-stub"

    # The repo URL comes from the secret store; point it at the local bare repo.
    secret_resolver = MagicMock()
    secret_resolver.get_secret.return_value = MagicMock(
        secret_user="aiworkbench_repo_url", secret_value=f"file://{bare}"
    )

    svc = GitSyncService(
        cache_plugin=MemoryCachePlugin(),
        skill_service_factory=MagicMock(),  # unused on the bootstrap clone path
        config=config,
        oss_storage=oss,
        secret_resolver=secret_resolver,
        repo_url_secret_name="aiworkbench-repo-url-secret",
    )

    result = asyncio.run(svc.sync_bootstrap())

    assert result["success"] is True, result
    assert result["method"] == "clone", result
    assert config.local_bare_repo.exists()
    assert (config.skills_target / "business" / "demo" / "SKILL.md").is_file()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
@pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("tar") is None,
    reason="rsync/tar not available",
)
def test_singlebox_startup_clones_when_local_skills_repo_missing(
    local_skills_bare_repo, tmp_path, monkeypatch
):
    bare = local_skills_bare_repo
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.setenv("GIT_SYNC_TMP_BASE", str(tmp_path / "git-tmp"))

    config = GitSyncConfig()
    config.local_bare_repo = tmp_path / "clone" / "aiworkbench.git"
    config.skills_target = tmp_path / "missing-skills-repo"
    config.subtrees[0]["target_dir"] = config.skills_target
    config.enable_oss_sync = False

    skill_service = MagicMock()
    skill_service.sync_skills_from_git.return_value = {"updated": 1}
    skill_service._refresh_market_cache.return_value = {
        "cache_refreshed": True,
        "skills_count": 1,
    }
    factory = MagicMock()
    factory.create.return_value = skill_service

    secret_resolver = MagicMock()
    secret_resolver.get_secret.return_value = MagicMock(
        secret_user="aiworkbench_repo_url", secret_value=f"file://{bare}"
    )

    svc = GitSyncService(
        cache_plugin=MemoryCachePlugin(),
        skill_service_factory=factory,
        config=config,
        oss_storage=MagicMock(spec=ObjectStoragePlugin),
        secret_resolver=secret_resolver,
        allow_missing_repo_url=True,
        repo_url_secret_name="aiworkbench-repo-url-secret",
    )

    try:
        asyncio.run(svc.startup())
    finally:
        svc._executor.shutdown(wait=True)

    assert config.local_bare_repo.exists()
    assert (config.skills_target / "business" / "demo" / "SKILL.md").is_file()
    skill_service.sync_skills_from_git.assert_called_once_with(git_renames={})
    skill_service._refresh_market_cache.assert_called_once()


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
@pytest.mark.skipif(
    shutil.which("rsync") is None or shutil.which("tar") is None,
    reason="rsync/tar not available",
)
def test_bootstrap_repairs_missing_skills_target_when_bare_repo_exists(
    local_skills_bare_repo, tmp_path, monkeypatch
):
    monkeypatch.setenv("GIT_SYNC_TMP_BASE", str(tmp_path / "git-tmp"))

    config = GitSyncConfig()
    config.local_bare_repo = local_skills_bare_repo
    config.skills_target = tmp_path / "missing-skills-repo"
    config.subtrees[0]["target_dir"] = config.skills_target
    config.enable_oss_sync = False

    oss = MagicMock(spec=ObjectStoragePlugin)
    secret_resolver = MagicMock()
    secret_resolver.get_secret.return_value = MagicMock(
        secret_user="aiworkbench_repo_url",
        secret_value=f"file://{local_skills_bare_repo}",
    )

    svc = GitSyncService(
        cache_plugin=MemoryCachePlugin(),
        skill_service_factory=MagicMock(),
        config=config,
        oss_storage=oss,
        secret_resolver=secret_resolver,
        repo_url_secret_name="aiworkbench-repo-url-secret",
    )

    try:
        result = asyncio.run(svc.sync_bootstrap())
    finally:
        svc._executor.shutdown(wait=True)

    assert result["success"] is True, result
    assert result["method"] == "existing_repaired", result
    assert (config.skills_target / "business" / "demo" / "SKILL.md").is_file()
    assert (config.skills_target / ".skills-version").is_file()


def _service_for_bootstrap_branch(tmp_path):
    config = GitSyncConfig()
    config.local_bare_repo = tmp_path / "aiworkbench.git"
    config.skills_target = tmp_path / "skills-repo"
    config.subtrees[0]["target_dir"] = config.skills_target
    config.enable_oss_sync = False

    secret_resolver = MagicMock()
    secret_resolver.get_secret.return_value = MagicMock(
        secret_user="aiworkbench_repo_url",
        secret_value="file:///tmp/aiworkbench.git",
    )
    return GitSyncService(
        cache_plugin=MemoryCachePlugin(),
        skill_service_factory=MagicMock(),
        config=config,
        oss_storage=MagicMock(spec=ObjectStoragePlugin),
        secret_resolver=secret_resolver,
        repo_url_secret_name="aiworkbench-repo-url-secret",
    )


def test_bootstrap_clone_falls_back_to_oss_when_fetch_fails(tmp_path):
    svc = _service_for_bootstrap_branch(tmp_path)
    svc._clone_bare_repo = AsyncMock()
    svc._git_fetch = AsyncMock(return_value={"success": False, "error": "fetch boom"})
    svc._download_from_oss_and_extract = AsyncMock()

    try:
        result = asyncio.run(svc._bootstrap_clone_or_fallback())
    finally:
        svc._executor.shutdown(wait=True)

    assert result == {"success": True, "method": "oss_fallback"}
    svc._download_from_oss_and_extract.assert_awaited_once()


def test_bootstrap_clone_reports_failed_when_subtree_and_oss_fail(tmp_path):
    svc = _service_for_bootstrap_branch(tmp_path)
    svc._clone_bare_repo = AsyncMock()
    svc._git_fetch = AsyncMock(return_value={"success": True})
    svc._sync_subtree = AsyncMock(return_value={"success": False, "error": "bad subtree"})
    svc._download_from_oss_and_extract = AsyncMock(side_effect=RuntimeError("oss boom"))

    try:
        result = asyncio.run(svc._bootstrap_clone_or_fallback())
    finally:
        svc._executor.shutdown(wait=True)

    assert result["success"] is False
    assert result["method"] == "failed"
    assert "oss boom" in result["error"]


def test_bootstrap_existing_result_propagates_repair_error(tmp_path):
    svc = _service_for_bootstrap_branch(tmp_path)
    svc._ensure_skills_subtree_ready = AsyncMock(
        return_value={
            "success": False,
            "method": "existing_repair_failed",
            "error": "repair boom",
        }
    )

    try:
        result = asyncio.run(svc._bootstrap_existing_result({"success": False}))
    finally:
        svc._executor.shutdown(wait=True)

    assert result["success"] is False
    assert result["method"] == "existing_repair_failed"
    assert result["error"] == "repair boom"
    assert result["subtree"] is None


def test_ensure_skills_subtree_ready_reports_fetch_failure(tmp_path):
    svc = _service_for_bootstrap_branch(tmp_path)
    svc.config.local_bare_repo.mkdir(parents=True)
    svc._git_fetch = AsyncMock(return_value={"success": False, "error": "fetch boom"})

    try:
        result = asyncio.run(svc._ensure_skills_subtree_ready())
    finally:
        svc._executor.shutdown(wait=True)

    assert result["success"] is False
    assert result["method"] == "existing_repair_failed"
    assert "fetch boom" in result["error"]


def test_ensure_skills_subtree_ready_reports_extract_failure(tmp_path):
    svc = _service_for_bootstrap_branch(tmp_path)
    svc.config.local_bare_repo.mkdir(parents=True)
    svc._git_fetch = AsyncMock(return_value={"success": True})
    svc._sync_subtree = AsyncMock(return_value={"success": False, "error": "extract boom"})

    try:
        result = asyncio.run(svc._ensure_skills_subtree_ready())
    finally:
        svc._executor.shutdown(wait=True)

    assert result["success"] is False
    assert result["method"] == "existing_repair_failed"
    assert "extract boom" in result["error"]
