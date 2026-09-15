"""Unit tests for TaskDiscoveryModule — URL resolution + SessionInitiator base binding.

Covers _resolve_frontend_url, _resolve_backend_url env branches, and
_provide_session_initiator: OpenApiBotPort bound → OpenApiBotSessionInitiator
(唯一实现), port 未绑定/None → UnavailableSessionInitiator fail-closed 占位
(2026-09-15 统一化: 原 CronRelay 基绑定已废除)。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.task.task_discovery.session_initiator import (
    OpenApiBotSessionInitiator,
    UnavailableSessionInitiator,
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


class TestProvideSessionInitiator:
    """Verify the unified base binding (2026-09-15 统一化).

    provider 不再按 DEPLOY_PROFILE 分发, solely 依赖 DI 提供的 ``OpenApiBotPort``:
    - 绑定成功 → ``OpenApiBotSessionInitiator`` (唯一实现, 原 corp 下沉)。
    - 未绑定/解析失败/None → ``UnavailableSessionInitiator`` fail-closed 占位
      (session 创建调用即抛可读错误, 由 DiscoveryService per-bot 容错记录)。
    """

    def _make_module(self):
        return TaskDiscoveryModule()

    def _make_injector(self, fe, port):
        """Mock injector: 第一次 get(ConfigFrontendUrlProvider) → fe, 第二次 get(OpenApiBotPort) → port."""
        injector = MagicMock()
        injector.get.side_effect = [fe, port]
        return injector

    @pytest.mark.parametrize("profile", ["singlebox", "test", "community", "corp"])
    def test_port_bound_returns_openapi_bot_impl(self, monkeypatch, profile):
        """Any profile → port 绑定成功即返回 OpenApiBotSessionInitiator (无 profile 分支)."""
        monkeypatch.setenv("DEPLOY_PROFILE", profile)
        fake_port = MagicMock()
        injector = self._make_injector(MagicMock(), fake_port)
        result = self._make_module()._provide_session_initiator(injector)
        assert isinstance(result, OpenApiBotSessionInitiator)
        assert result._openapi_bot is fake_port

    def test_frontend_url_provider_bound_is_injected(self, monkeypatch):
        """ConfigFrontendUrlProvider resolves → injected into the initiator."""
        from agentclaw.community.core.task.task_discovery.frontend_url import (
            ConfigFrontendUrlProvider,
        )

        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        fake_fe = MagicMock()
        injector = self._make_injector(fake_fe, MagicMock())
        result = self._make_module()._provide_session_initiator(injector)
        assert isinstance(result, OpenApiBotSessionInitiator)
        assert result._frontend_url_provider is fake_fe
        assert injector.get.call_count == 2
        from agentclaw.community.core.task.task_runner.client.ports import (
            OpenApiBotPort,
        )
        injector.get.assert_any_call(ConfigFrontendUrlProvider)
        injector.get.assert_any_call(OpenApiBotPort)

    def test_frontend_url_provider_unbound_uses_empty_default(self, monkeypatch):
        """ConfigFrontendUrlProvider resolution raises → empty static fallback;
        OpenApiBotPort 正常 → OpenApiBotSessionInitiator."""
        from agentclaw.community.core.task.task_discovery.frontend_url import (
            ConfigFrontendUrlProvider,
        )
        from agentclaw.community.core.task.task_discovery.frontend_url import (
            FrontendUrlHolder,
        )

        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        FrontendUrlHolder._url = ""  # isolate from other holders
        injector = MagicMock()
        injector.get.side_effect = [Exception("fe not bound"), MagicMock()]
        result = self._make_module()._provide_session_initiator(injector)
        assert isinstance(result, OpenApiBotSessionInitiator)
        assert isinstance(result._frontend_url_provider, ConfigFrontendUrlProvider)
        assert result._frontend_url_provider.get() == ""

    def test_port_unbound_returns_unavailable_placeholder(self, monkeypatch):
        """OpenApiBotPort DI 解析抛错（社区/单机列无凭证）→ UnavailableSessionInitiator."""
        monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
        injector = MagicMock()
        injector.get.side_effect = [
            MagicMock(),  # fe provider ok
            Exception("OpenApiBotPort not bound"),
        ]
        result = self._make_module()._provide_session_initiator(injector)
        assert isinstance(result, UnavailableSessionInitiator)

    def test_port_none_returns_unavailable_placeholder(self, monkeypatch):
        """OpenApiBotPort resolved to None (corp fail-closed) → UnavailableSessionInitiator."""
        monkeypatch.setenv("DEPLOY_PROFILE", "corp")
        injector = self._make_injector(MagicMock(), None)
        result = self._make_module()._provide_session_initiator(injector)
        assert isinstance(result, UnavailableSessionInitiator)
