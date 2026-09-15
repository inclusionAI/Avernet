"""Tests for GitSyncConfig."""
import asyncio
from unittest.mock import patch

import pytest

from agentclaw.community.core.skill_center.services.git_sync import GitSyncConfig


@pytest.fixture
def mock_bolt_shared(tmp_path):
    """Mock get_bolt_shared_dir to return a temp directory."""
    with patch(
        "agentclaw.community.core.workspace.path_factory.get_bolt_shared_dir",
        return_value=tmp_path
    ):
        yield tmp_path


def test_bootstrap_wait_timeout_default(mock_bolt_shared):
    """Test that bootstrap_wait_timeout has correct default value."""
    cfg = GitSyncConfig()
    assert cfg.bootstrap_wait_timeout == 60


def test_bootstrap_wait_timeout_from_env(mock_bolt_shared, monkeypatch):
    """Test that bootstrap_wait_timeout can be set from environment variable."""
    monkeypatch.setenv("BOOTSTRAP_WAIT_TIMEOUT", "120")
    cfg = GitSyncConfig()
    assert cfg.bootstrap_wait_timeout == 120


def test_enable_oss_sync_yaml_load_swallows_exception(
    mock_bolt_shared, monkeypatch
):
    """yaml 解析异常时 enable_oss_sync 走 except 分支被吞掉，保持 False。"""
    monkeypatch.delenv("ENABLE_OSS_SYNC", raising=False)
    with patch("yaml.safe_load", side_effect=RuntimeError("yaml parse boom")):
        cfg = GitSyncConfig()
    # except Exception: pass 分支生效，配置回到默认 False
    assert cfg.enable_oss_sync is False


# The secret-registry key name is now deployment config (SecretNamesConfig);
# tests pass an explicit name rather than relying on a hardcoded constant.
_TEST_REPO_SECRET_NAME = "aiworkbench-repo-url-secret"


def _svc(
    config,
    secret_resolver,
    *,
    allow_missing_repo_url=False,
    repo_url_secret_name=_TEST_REPO_SECRET_NAME,
):
    from unittest.mock import MagicMock

    from agentclaw.community.core.skill_center.services.git_sync import GitSyncService

    return GitSyncService(
        cache_plugin=MagicMock(),
        skill_service_factory=MagicMock(),
        config=config,
        oss_storage=MagicMock(),
        secret_resolver=secret_resolver,
        allow_missing_repo_url=allow_missing_repo_url,
        repo_url_secret_name=repo_url_secret_name,
    )


def test_repo_url_resolved_from_secret(mock_bolt_shared):
    """The repo URL is read through the SecretResolver contract."""
    from unittest.mock import MagicMock

    config = GitSyncConfig()
    assert not hasattr(config, "repo_url")  # not a config field anymore

    secret = MagicMock(
        secret_user="aiworkbench_repo_url",
        secret_value="https://host/repo.git",
    )
    resolver = MagicMock()
    resolver.get_secret.return_value = secret

    svc = _svc(config, resolver, allow_missing_repo_url=True)
    assert svc._repo_url == "https://host/repo.git"
    resolver.get_secret.assert_called_once_with(_TEST_REPO_SECRET_NAME)
    svc._executor.shutdown(wait=True)


def test_construction_fails_loudly_when_secret_absent(mock_bolt_shared, monkeypatch):
    """No repo-URL secret ⇒ GitSyncService construction raises (fail-early, no
    silent inert). A profile without the secret — e.g. community — therefore
    cannot construct it (lifecycle discovery skips it)."""
    from unittest.mock import MagicMock

    monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
    config = GitSyncConfig()
    resolver = MagicMock()
    resolver.get_secret.return_value = None  # no such secret

    with pytest.raises(RuntimeError, match="Skills repo URL not found"):
        _svc(config, resolver)


def test_construction_fails_when_secret_value_empty(mock_bolt_shared, monkeypatch):
    """An empty secret value is treated as missing (fail-early)."""
    from unittest.mock import MagicMock

    monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
    config = GitSyncConfig()
    resolver = MagicMock()
    resolver.get_secret.return_value = MagicMock(secret_value="")

    with pytest.raises(RuntimeError, match="Skills repo URL not found"):
        _svc(config, resolver)


def test_singlebox_allows_missing_repo_secret(mock_bolt_shared, monkeypatch):
    """singlebox uses an existing local skills-repo, not the corp repo secret."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    config = GitSyncConfig()
    resolver = MagicMock()
    resolver.get_secret.return_value = None

    svc = _svc(config, resolver, allow_missing_repo_url=True)

    assert svc._repo_url is None
    resolver.get_secret.assert_called_once_with(_TEST_REPO_SECRET_NAME)
    svc._executor.shutdown(wait=True)


def test_empty_secret_name_skips_resolver_and_allows_missing(
    mock_bolt_shared, monkeypatch
):
    """No configured secret name (e.g. community) with a permissive profile →
    the resolver is never called and the repo URL is None (git-sync degrades to
    its on-disk source)."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("DEPLOY_PROFILE", "community")
    config = GitSyncConfig()
    resolver = MagicMock()

    svc = _svc(
        config, resolver, allow_missing_repo_url=True, repo_url_secret_name=""
    )

    assert svc._repo_url is None
    resolver.get_secret.assert_not_called()
    svc._executor.shutdown(wait=True)


def test_empty_secret_name_strict_profile_fails_loudly(mock_bolt_shared, monkeypatch):
    """No configured secret name on a strict (prod-like) profile still fails
    early — never silently inert."""
    from unittest.mock import MagicMock

    monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
    config = GitSyncConfig()
    resolver = MagicMock()

    with pytest.raises(RuntimeError, match="Skills repo URL not found"):
        _svc(config, resolver, allow_missing_repo_url=False, repo_url_secret_name="")
    resolver.get_secret.assert_not_called()


def test_singlebox_startup_seeds_market_from_local_skills_repo(
    mock_bolt_shared, tmp_path, monkeypatch
):
    """When the repo secret is absent, singlebox hydrates DB/cache from disk."""
    from unittest.mock import MagicMock
    from agentclaw.community.core.skill_center.services.git_sync import GitSyncService

    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    skills_repo = tmp_path / "skills-repo"
    skills_repo.mkdir()

    config = GitSyncConfig()
    config.skills_target = skills_repo
    resolver = MagicMock()
    resolver.get_secret.return_value = None

    skill_service = MagicMock()
    skill_service.sync_skills_from_git.return_value = {"updated": 1}
    skill_service._refresh_market_cache.return_value = {
        "cache_refreshed": True,
        "skills_count": 1,
    }
    factory = MagicMock()
    factory.create.return_value = skill_service

    svc = GitSyncService(
        cache_plugin=MagicMock(),
        skill_service_factory=factory,
        config=config,
        oss_storage=MagicMock(),
        secret_resolver=resolver,
        allow_missing_repo_url=True,
    )

    try:
        asyncio.run(svc.startup())
    finally:
        svc._executor.shutdown(wait=True)

    skill_service.sync_skills_from_git.assert_called_once_with(git_renames={})
    skill_service._refresh_market_cache.assert_called_once()
    assert svc._started is False


def test_singlebox_bootstrap_reports_missing_local_skills_repo(
    mock_bolt_shared, tmp_path, monkeypatch
):
    """Missing secret + missing local skills-repo is reported without git/OSS."""
    from unittest.mock import MagicMock
    from agentclaw.community.core.skill_center.services.git_sync import GitSyncService

    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    config = GitSyncConfig()
    config.skills_target = tmp_path / "missing-skills-repo"
    resolver = MagicMock()
    resolver.get_secret.return_value = None

    svc = GitSyncService(
        cache_plugin=MagicMock(),
        skill_service_factory=MagicMock(),
        config=config,
        oss_storage=MagicMock(),
        secret_resolver=resolver,
        allow_missing_repo_url=True,
    )

    try:
        result = asyncio.run(svc.sync_bootstrap())
        local_result = asyncio.run(svc._sync_existing_local_market())
    finally:
        svc._executor.shutdown(wait=True)

    assert result["success"] is False
    assert result["method"] == "local_missing"
    assert "local skills repo not found" in result["error"]
    assert local_result["success"] is False
    assert local_result["method"] == "local_missing"


# ---------------------------------------------------------------------------
# enable_skill_sync gate (community deployments disable skill git sync)
# ---------------------------------------------------------------------------


class _FakeSofaConfig:
    """Stands in for the lazy sofa handle; model_dump() mirrors AppConfig."""

    def __init__(self, user_config):
        self.user_config = user_config

    def model_dump(self):
        return {"user_config": self.user_config}


def _patch_sofa_config(monkeypatch, user_config):
    # GitSyncConfig does ``from agentclaw.community.core.config import sofa``
    # at call time, so patching the module attribute is picked up on the
    # next construction.
    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config",
        _FakeSofaConfig(user_config),
    )


def test_enable_skill_sync_defaults_true(mock_bolt_shared, monkeypatch):
    """No git_sync block in the merged config → sync stays enabled."""
    _patch_sofa_config(monkeypatch, {})
    cfg = GitSyncConfig()
    assert cfg.enable_skill_sync is True


def test_enable_skill_sync_false_from_config(mock_bolt_shared, monkeypatch):
    """user_config.git_sync.enable_skill_sync=false is respected."""
    _patch_sofa_config(
        monkeypatch, {"git_sync": {"enable_skill_sync": False}}
    )
    cfg = GitSyncConfig()
    assert cfg.enable_skill_sync is False


def test_enable_skill_sync_bool_coercion(mock_bolt_shared, monkeypatch):
    """A truthy non-bool value keeps sync enabled (no strict typing here)."""
    _patch_sofa_config(
        monkeypatch, {"git_sync": {"enable_skill_sync": "false"}}
    )
    cfg = GitSyncConfig()
    # YAML anchors aside, the overlay ships a real boolean; a truthy string
    # (e.g. "false" in an env-substituted overlay) only warns by still
    # enabling sync — disabling must be explicit.
    assert cfg.enable_skill_sync == "false"


def test_enable_skill_sync_config_exception_keeps_default(
    mock_bolt_shared, monkeypatch
):
    """sofa read raising → except branch keeps the enabled default."""
    class _Boom:
        def model_dump(self):
            raise RuntimeError("config backend unreachable")

    monkeypatch.setattr(
        "agentclaw.community.core.config.sofa.sofa_config", _Boom()
    )
    cfg = GitSyncConfig()
    assert cfg.enable_skill_sync is True


def test_startup_skips_all_sync_when_disabled(mock_bolt_shared, monkeypatch):
    """enable_skill_sync=False → startup returns before any sync work."""
    from unittest.mock import MagicMock
    from agentclaw.community.core.skill_center.services.git_sync import GitSyncService

    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    _patch_sofa_config(
        monkeypatch, {"git_sync": {"enable_skill_sync": False}}
    )
    config = GitSyncConfig()
    assert config.enable_skill_sync is False

    svc = GitSyncService(
        cache_plugin=MagicMock(),
        skill_service_factory=MagicMock(),
        config=config,
        oss_storage=MagicMock(),
        secret_resolver=MagicMock(),
        allow_missing_repo_url=True,
    )

    # The gate must short-circuit before bootstrap/db sync/periodic sync —
    # none of these may run.
    with (
        patch.object(svc, "sync_bootstrap", new_callable=MagicMock) as boot,
        patch.object(
            svc, "_sync_existing_local_market", new_callable=MagicMock
        ) as seed,
        patch.object(
            svc, "start_periodic_sync", new_callable=MagicMock
        ) as periodic,
    ):
        try:
            asyncio.run(svc.startup())
        finally:
            svc._executor.shutdown(wait=True)

    boot.assert_not_called()
    seed.assert_not_called()
    periodic.assert_not_called()
    assert svc._started is False
