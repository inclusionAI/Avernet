"""Unit tests for TaskDiscoveryModule's deployment-neutral session wiring."""

from __future__ import annotations

from unittest.mock import MagicMock

_NOT_GIVEN = object()

from agentclaw.community.core.task.task_discovery.frontend_url import (
    ConfigFrontendUrlProvider,
)
from agentclaw.community.core.task.task_discovery.session_initiator import (
    OpenApiBotSessionInitiator,
    UnavailableSessionInitiator,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.di.modules.infrastructure.community.task_runner_integration import (
    BcsTokenProviderImpl,
)
from agentclaw.community.di.modules.task_discovery_module import (
    TaskDiscoveryModule,
    _backend_origin_from_callback_url,
)


def test_backend_origin_uses_the_standard_task_callback_url() -> None:
    provider = BcsTokenProviderImpl(
        task_callback_url="https://backend.example.test/api/callback"
    )
    assert _backend_origin_from_callback_url(provider) == "https://backend.example.test"


def test_backend_origin_falls_back_for_missing_or_invalid_callback_url() -> None:
    assert _backend_origin_from_callback_url(None) == "http://localhost:8888"
    assert (
        _backend_origin_from_callback_url(BcsTokenProviderImpl(task_callback_url=""))
        == "http://localhost:8888"
    )
    assert (
        _backend_origin_from_callback_url(
            BcsTokenProviderImpl(task_callback_url="not-a-url")
        )
        == "http://localhost:8888"
    )


class TestProvideSessionInitiator:
    """Verify configuration-driven assembly without deployment-profile branches."""

    def _make_injector(self, frontend, bcs_provider, port):
        injector = MagicMock()
        injector.get.side_effect = [frontend, bcs_provider, port]
        return injector

    def _make(self, frontend=_NOT_GIVEN, bcs_provider=_NOT_GIVEN, port=_NOT_GIVEN):
        frontend = MagicMock() if frontend is _NOT_GIVEN else frontend
        bcs_provider = (
            BcsTokenProviderImpl(task_callback_url="https://backend.example.test")
            if bcs_provider is _NOT_GIVEN
            else bcs_provider
        )
        port = MagicMock() if port is _NOT_GIVEN else port
        injector = self._make_injector(frontend, bcs_provider, port)
        return TaskDiscoveryModule()._provide_session_initiator(injector), injector

    def test_bound_ports_use_the_production_session_initiator(self):
        fake_port = MagicMock()
        provider = BcsTokenProviderImpl(
            task_callback_url="https://backend.example.test"
        )
        fake_frontend = MagicMock()
        result, injector = self._make(fake_frontend, provider, fake_port)

        assert isinstance(result, OpenApiBotSessionInitiator)
        assert result._openapi_bot is fake_port
        assert result._frontend_url_provider is fake_frontend
        assert result._backend_url == "https://backend.example.test"
        injector.get.assert_any_call(ConfigFrontendUrlProvider)
        injector.get.assert_any_call(OpenApiBotPort)

    def test_missing_bcs_provider_uses_the_local_backend_default(self):
        result, _ = self._make(bcs_provider=None, port=MagicMock())
        assert isinstance(result, OpenApiBotSessionInitiator)
        assert result._backend_url == "http://localhost:8888"

    def test_missing_frontend_provider_uses_the_local_static_default(self):
        result, _ = self._make(
            frontend=Exception("frontend provider not bound"),
            port=MagicMock(),
        )
        assert isinstance(result, OpenApiBotSessionInitiator)
        assert isinstance(result._frontend_url_provider, ConfigFrontendUrlProvider)
        assert result._frontend_url == "http://localhost:8000"

    def test_openapi_port_error_returns_unavailable_placeholder(self):
        result, _ = self._make(
            port=Exception("OpenApiBotPort not bound"),
        )
        assert isinstance(result, UnavailableSessionInitiator)

    def test_openapi_port_none_returns_unavailable_placeholder(self):
        result, _ = self._make(port=None)
        assert isinstance(result, UnavailableSessionInitiator)
