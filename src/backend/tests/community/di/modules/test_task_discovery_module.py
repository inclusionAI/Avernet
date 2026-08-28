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
        """Non-singlebox + OpenApiBotPort resolves → OpenApiBotSessionInitiator.

        corp 分支还会二次解析 ``FrontendUrlProvider``(corp 绑
        ``CorpFrontendUrlProvider``;此处 MagicMock 同样返回 fake,
        传入 initiator 供 _build_session_url 使用)。"""
        from agentclaw.community.core.task.task_discovery.frontend_url_provider import (
            FrontendUrlProvider,
        )

        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        injector = MagicMock()
        fake_bot = MagicMock()
        injector.get.return_value = fake_bot
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, OpenApiBotSessionInitiator)
        assert result._frontend_url_provider is fake_bot
        # 两次 DI 解析:OpenApiBotPort + FrontendUrlProvider
        assert injector.get.call_count == 2
        injector.get.assert_any_call(FrontendUrlProvider)

    def test_corp_path_frontend_url_provider_unbound_uses_null(self, monkeypatch):
        """Non-singlebox + FrontendUrlProvider 解析抛错 → Null 兜底(回落构造值)。

        OpenApiBotPort 正常返回,但 FrontendUrlProvider 未绑定(injector.get
        第二次调用 raise)→ NullFrontendUrlProvider;initiator 仍装配成功。"""
        from agentclaw.community.core.task.task_discovery.frontend_url_provider import (
            FrontendUrlProvider,
            NullFrontendUrlProvider,
        )

        monkeypatch.setenv("DEPLOY_PROFILE", "test")
        injector = MagicMock()
        fake_bot = MagicMock()

        def _get(interface):
            if interface is FrontendUrlProvider:
                raise Exception("not bound")
            return fake_bot

        injector.get.side_effect = _get
        result = self._make_module()._provide_session_initiator(
            self._make_cron_relay(), injector,
        )
        assert isinstance(result, OpenApiBotSessionInitiator)
        assert isinstance(result._frontend_url_provider, NullFrontendUrlProvider)

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


class TestInjectFrontendUrlFromYaml:
    @pytest.mark.parametrize(
        ("env", "expected"),
        [("pre", "http://pre.example.com"), ("prod", "http://prod.example.com")],
    )
    def test_uses_environment_specific_yaml_url(self, env, expected):
        from agentclaw.community.core.task.task_discovery.session_initiator import (
            FrontendUrlHolder,
        )
        from agentclaw.community.di.modules import config_module
        from agentclaw.community.utils import env_utils

        FrontendUrlHolder.set("")
        config = {
            "frontend_url": "http://default.example.com",
            "frontend_url_pre": "http://pre.example.com",
            "frontend_url_prod": "http://prod.example.com",
        }
        try:
            with (
                pytest.MonkeyPatch.context() as monkeypatch,
            ):
                monkeypatch.setattr(config_module, "_block", lambda _name: config)
                monkeypatch.setattr(env_utils, "get_current_env", lambda: env)
                TaskDiscoveryModule._inject_frontend_url_from_yaml()

            assert FrontendUrlHolder.get() == expected
        finally:
            FrontendUrlHolder.set("")

    def test_does_not_override_runtime_frontend_url(self):
        from agentclaw.community.core.task.task_discovery.session_initiator import (
            FrontendUrlHolder,
        )
        from agentclaw.community.di.modules import config_module

        FrontendUrlHolder.set("http://runtime.example.com")
        try:
            with pytest.MonkeyPatch.context() as monkeypatch:
                block = MagicMock()
                monkeypatch.setattr(config_module, "_block", block)
                TaskDiscoveryModule._inject_frontend_url_from_yaml()

            assert FrontendUrlHolder.get() == "http://runtime.example.com"
            block.assert_not_called()
        finally:
            FrontendUrlHolder.set("")
