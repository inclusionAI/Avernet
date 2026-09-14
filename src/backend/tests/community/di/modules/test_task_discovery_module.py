"""Unit tests for TaskDiscoveryModule — URL resolution + SessionInitiator base binding.

Covers _resolve_frontend_url, _resolve_backend_url env branches, and
_provide_session_initiator's base (LOCAL default) binding with Null fallback.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.task.task_discovery.session_initiator import (
    CronRelaySessionInitiator,
)
from agentclaw.community.di.modules.task_discovery_module import (
    TaskDiscoveryModule,
    _resolve_backend_url,
    _resolve_frontend_url,
)


# ---------------------------------------------------------------------------
# _resolve_frontend_url
# ---------------------------------------------------------------------------

class TestResolveFrontendUrl:
    def test_frontend_url_env_takes_priority(self, monkeypatch):
        monkeypatch.setenv("FRONTEND_URL", "http://custom:9999")
        assert _resolve_frontend_url() == "http://custom:9999"

    def test_singlebox_uses_singlebox_frontend_url(self, monkeypatch):
        monkeypatch.delenv("FRONTEND_URL", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        monkeypatch.setenv("SINGLEBOX_FRONTEND_URL", "http://sb-fe:8000")
        assert _resolve_frontend_url() == "http://sb-fe:8000"

    def test_singlebox_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("FRONTEND_URL", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        monkeypatch.delenv("SINGLEBOX_FRONTEND_URL", raising=False)
        assert _resolve_frontend_url() == "http://localhost:8000"

    def test_non_singlebox_returns_default(self, monkeypatch):
        monkeypatch.delenv("FRONTEND_URL", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        assert _resolve_frontend_url() == "http://localhost:8000"


# ---------------------------------------------------------------------------
# _resolve_backend_url
# ---------------------------------------------------------------------------

class TestResolveBackendUrl:
    def test_backend_url_env_takes_priority(self, monkeypatch):
        monkeypatch.setenv("BACKEND_URL", "http://custom:7777")
        assert _resolve_backend_url() == "http://custom:7777"

    def test_singlebox_uses_singlebox_backend_url(self, monkeypatch):
        monkeypatch.delenv("BACKEND_URL", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        monkeypatch.setenv("SINGLEBOX_BACKEND_URL", "http://sb-be:8888")
        assert _resolve_backend_url() == "http://sb-be:8888"

    def test_singlebox_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("BACKEND_URL", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        monkeypatch.delenv("SINGLEBOX_BACKEND_URL", raising=False)
        assert _resolve_backend_url() == "http://localhost:8888"

    def test_non_singlebox_returns_default(self, monkeypatch):
        monkeypatch.delenv("BACKEND_URL", raising=False)
        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        assert _resolve_backend_url() == "http://localhost:8888"


# ---------------------------------------------------------------------------
# _provide_session_initiator — base binding (local default)
# ---------------------------------------------------------------------------

class TestProvideSessionInitiator:
    """Verify the base provider always binds the LOCAL impl (CronRelay) and
    injects FrontendUrlProvider with Null fallback.

    组合根选实现 (plugin-ication 改造): provider 不再按 DEPLOY_PROFILE 分发 —
    corp 列通过 ``CorpTaskIntegrationModule.session_initiator`` 的同键 provider
    (last-binding-wins) 覆盖为 ``OpenApiBotSessionInitiator`` (corp/plugins/prod)。
    本 base provider 在所有 profile 下都返回 ``CronRelaySessionInitiator``。
    """

    def _make_module(self):
        return TaskDiscoveryModule()

    def _make_cron_relay(self):
        return MagicMock()

    @pytest.mark.parametrize("profile", ["singlebox", "test", "community"])
    def test_base_returns_cron_relay_regardless_of_profile(self, monkeypatch, profile):
        """Any profile → base provider returns CronRelaySessionInitiator (no
        DEPLOY_PROFILE branching inside the provider)."""
        monkeypatch.setenv("DEPLOY_PROFILE", profile)
        injector = MagicMock()
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, CronRelaySessionInitiator)

    def test_frontend_url_provider_bound_is_injected(self, monkeypatch):
        """FrontendUrlProvider resolves → injected into the initiator."""
        from agentclaw.community.plugin_api.frontend_url import (
            FrontendUrlProvider,
        )

        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        injector = MagicMock()
        fake_fe = MagicMock()
        injector.get.return_value = fake_fe
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, CronRelaySessionInitiator)
        assert result._frontend_url_provider is fake_fe
        injector.get.assert_called_once_with(FrontendUrlProvider)

    def test_frontend_url_provider_unbound_uses_null(self, monkeypatch):
        """FrontendUrlProvider resolution raises → NullFrontendUrlProvider
        fallback (constructor default wins)."""
        from agentclaw.community.plugin_api.frontend_url import (
            NullFrontendUrlProvider,
        )

        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        injector = MagicMock()
        injector.get.side_effect = Exception("not bound")
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, CronRelaySessionInitiator)
        assert isinstance(result._frontend_url_provider, NullFrontendUrlProvider)
