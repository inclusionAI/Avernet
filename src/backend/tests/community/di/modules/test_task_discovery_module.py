"""Unit tests for TaskDiscoveryModule — URL resolution + SessionInitiator DI dispatch.

Covers _resolve_frontend_url, _resolve_backend_url env branches, and
_provide_session_initiator corp/singlebox/fallback dispatch.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.task.task_discovery.openapi_bot_session_initiator import (
    OpenApiBotSessionInitiator,
)
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
# _provide_session_initiator — DI dispatch
# ---------------------------------------------------------------------------

class TestProvideSessionInitiator:
    """Verify _provide_session_initiator dispatches by DEPLOY_PROFILE and
    OpenApiBotPort availability."""

    def _make_module(self):
        return TaskDiscoveryModule()

    def _make_cron_relay(self):
        return MagicMock()

    def test_corp_path_with_openapi_bot_bound(self, monkeypatch):
        """Non-singlebox + OpenApiBotPort resolves → OpenApiBotSessionInitiator."""
        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        injector = MagicMock()
        fake_bot = MagicMock()
        injector.get.return_value = fake_bot
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, OpenApiBotSessionInitiator)
        injector.get.assert_called_once()

    def test_corp_path_openapi_bot_none_falls_back(self, monkeypatch):
        """Non-singlebox + OpenApiBotPort resolves to None → CronRelaySessionInitiator."""
        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        injector = MagicMock()
        injector.get.return_value = None
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, CronRelaySessionInitiator)

    def test_corp_path_openapi_port_unbound_falls_back(self, monkeypatch):
        """Non-singlebox + injector.get raises → CronRelaySessionInitiator."""
        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        injector = MagicMock()
        injector.get.side_effect = Exception("not bound")
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, CronRelaySessionInitiator)

    def test_singlebox_path_returns_cron_relay(self, monkeypatch):
        """Singlebox → always CronRelaySessionInitiator (no OpenApiBotPort lookup)."""
        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        injector = MagicMock()
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, CronRelaySessionInitiator)
        # OpenApiBotPort should never be resolved in singlebox
        injector.get.assert_not_called()
