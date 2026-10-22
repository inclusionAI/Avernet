from typing import Annotated

from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator,
    NoopCallbackAuthenticator,
)
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.api.cron_relay_service import CronRelayServiceProtocol
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.api.task.forum_digest_service import (
    ForumDigestReportServiceProtocol,
)
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.forum.browsing.cron_setup import (
    BbsBrowseCronManager,
)
from agentclaw.community.di.config import TaskDispatchConfig
from agentclaw.community.plugin_api.notify_sender import (
    QUALIFIER_BBS_DIGEST,
    NotifySenderPlugin,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
    InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    TaskSettingsServiceProtocol,
)
from agentclaw.community.core.task.task_discovery.frontend_url import (
    ConfigFrontendUrlProvider,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.di.modules.task_module import TaskModule

from agentclaw.community.di.modules.task_module import _resolve_harness_enabled
from agentclaw.community.core.task.task_runner.client.catalog_keyword_discover import (
    CatalogKeywordBotDiscover,
)


class _StubDiscoverModule(Module):
    """BotDiscoverServiceProtocol stub:TaskService 构造需 discover 端口(端口缺省时 stub)。"""

    @singleton
    @provider
    def discover(self) -> BotDiscoverServiceProtocol:
        class _D:
            def search_by_keyword(self, **kw):
                return {"total": 0, "items": []}

        return _D()  # type: ignore[return-value]

    @singleton
    @provider
    def bot_public(self) -> BotPublicServiceProtocol:
        """TaskService provider 还 inject BotPublicServiceProtocol(_resolve_discover 单 box 时不用);stub 占位。"""

        class _BP:
            def search_catalog_public_bots_by_keyword(self, **kw):
                return {"total": 0, "items": []}

        return _BP()  # type: ignore[return-value]


class _StubBbsDigestModule(Module):
    """Fixture-only deployment bindings for the BBS digest report service."""

    @singleton
    @provider
    def database(self) -> DatabasePlugin:
        return object()  # type: ignore[return-value]

    @singleton
    @provider
    def bot_service(self) -> BotServiceProtocol:
        class _BotService:
            def get_bot_by_id(self, bot_id: str):
                return {"bot_id": bot_id, "owner_id": "111111"}

        return _BotService()  # type: ignore[return-value]

    @singleton
    @provider
    def openapi_bot(self) -> OpenApiBotPort:
        class _OpenApiBot:
            @property
            def api_key_prefix(self) -> str:
                return "test-prefix"

        return _OpenApiBot()  # type: ignore[return-value]

    @singleton
    @provider
    def notify_messages(self) -> Annotated[NotifySenderPlugin, QUALIFIER_BBS_DIGEST]:
        class _NotifyMessages:
            def send(self, message, *, channel="tc_card"):
                return "notify-1"

        return _NotifyMessages()  # type: ignore[return-value]

    @singleton
    @provider
    def frontend_url(self) -> ConfigFrontendUrlProvider:
        return ConfigFrontendUrlProvider("http://frontend.example")


class _StubBbsCronModule(Module):
    """BBS cron-manager dependencies with fixture-only values."""

    @singleton
    @provider
    def cron_relay(self) -> CronRelayServiceProtocol:
        return object()  # type: ignore[return-value]

    @singleton
    @provider
    def task_dispatch(self) -> TaskDispatchConfig:
        return TaskDispatchConfig(
            backend_url="http://backend.example",
            task_search_skill_enabled=False,
            skill_report_enabled=True,
        )


def test_task_module_wires_bbs_browse_cron_manager():
    injector = Injector([TaskModule(), _StubDiscoverModule(), _StubBbsCronModule()])
    manager = injector.get(BbsBrowseCronManager)
    assert manager is injector.get(BbsBrowseCronManager)
    assert manager._backend_base_url == "http://backend.example"


def test_task_module_binds_callback_singletons():
    inj = Injector([TaskModule(), _StubDiscoverModule()])
    # 新增 callback 服务绑定
    assert isinstance(
        inj.get(CallbackCorrelationRegistry), InMemoryCallbackCorrelationRegistry
    )
    assert isinstance(inj.get(CallbackAuthenticator), NoopCallbackAuthenticator)
    # 既有 TaskServiceProtocol 仍可解析(端口接线未被破坏)
    assert isinstance(inj.get(TaskServiceProtocol).__class__.__name__, str)
    # singleton:两次取同对象
    assert inj.get(TaskServiceProtocol) is inj.get(TaskServiceProtocol)
    assert inj.get(CallbackCorrelationRegistry) is inj.get(CallbackCorrelationRegistry)
    assert inj.get(CallbackAuthenticator) is inj.get(CallbackAuthenticator)


def test_task_module_uses_keyword_discovery_in_every_profile(monkeypatch):
    class _BotPublic:
        def search_catalog_public_bots_by_keyword(self, **_kwargs):
            return {"total": 0, "items": []}

    for profile in ("singlebox", "corp", "community"):
        monkeypatch.setenv("DEPLOY_PROFILE", profile)
        discover = TaskModule._resolve_discover(bot_public=_BotPublic())
        assert isinstance(discover, CatalogKeywordBotDiscover)


class _FakeSettings(TaskSettingsServiceProtocol):
    """TaskSettingsServiceProtocol fake:is_enabled 返回固定布尔,模拟 KV 开关。"""

    def __init__(self, enabled: bool, exc: Exception | None = None) -> None:
        self._enabled = enabled
        self._exc = exc
        self.calls: list[str] = []

    def is_enabled(self, setting_type: str) -> bool:
        self.calls.append(setting_type)
        if self._exc is not None:
            raise self._exc
        return self._enabled

    def get_enabled(self, *, setting_type, env):
        return self._enabled

    def set_enabled(self, *, setting_type, enabled, env, operator=None):
        self._enabled = enabled
        return enabled


def test_resolve_harness_enabled_uses_settings_when_true(monkeypatch):
    """settings 服务返回 True → 开启 harness,不查 env。"""
    monkeypatch.delenv("OCB_TASK_HARNESS_ENABLED", raising=False)
    fake = _FakeSettings(enabled=True)
    assert _resolve_harness_enabled(fake) is True
    assert fake.calls == ["harness_poller"]


def test_resolve_harness_enabled_uses_settings_when_false(monkeypatch):
    """settings 服务返回 False 且 env 未设 → 关闭 harness。"""
    monkeypatch.delenv("OCB_TASK_HARNESS_ENABLED", raising=False)
    fake = _FakeSettings(enabled=False)
    assert _resolve_harness_enabled(fake) is False
    assert fake.calls == ["harness_poller"]


def test_resolve_harness_enabled_falls_back_to_env_when_settings_none(monkeypatch):
    """settings 服务未绑(None) → 回退 env OCB_TASK_HARNESS_ENABLED。"""
    monkeypatch.setenv("OCB_TASK_HARNESS_ENABLED", "1")
    assert _resolve_harness_enabled(None) is True
    monkeypatch.setenv("OCB_TASK_HARNESS_ENABLED", "0")
    assert _resolve_harness_enabled(None) is False
    monkeypatch.delenv("OCB_TASK_HARNESS_ENABLED", raising=False)
    assert _resolve_harness_enabled(None) is False


def test_resolve_harness_enabled_falls_back_to_env_on_read_error(monkeypatch):
    """settings 服务 is_enabled 抛异常 → 回退 env,不抛出。"""
    monkeypatch.delenv("OCB_TASK_HARNESS_ENABLED", raising=False)
    fake = _FakeSettings(enabled=True, exc=RuntimeError("kv down"))
    assert _resolve_harness_enabled(fake) is False  # env 关 → 关
    monkeypatch.setenv("OCB_TASK_HARNESS_ENABLED", "true")
    assert _resolve_harness_enabled(fake) is True  # env 开 → 开
    monkeypatch.delenv("OCB_TASK_HARNESS_ENABLED", raising=False)


def test_task_service_degrades_when_merchant_bindings_are_not_registered(monkeypatch):
    """A lightweight injector may omit optional merchant bindings."""
    from agentclaw.community.di.config import TaskDispatchConfig
    from agentclaw.community.di.modules import task_module as module

    class _Graph:
        def bind_repository(self, _repo):
            raise RuntimeError("repository not configured")

    class _Injector:
        def get(self, _binding):
            raise RuntimeError("optional binding not configured")

    monkeypatch.setattr(
        module.TaskModule, "_resolve_discover", staticmethod(lambda **_: object())
    )
    monkeypatch.setenv("OCB_TASK_HARNESS_ENABLED", "0")
    svc = module.TaskModule().task_service(
        _Graph(), object(),
        TaskDispatchConfig(backend_url="https://backend.example"),
        _Injector(),
    )
    assert isinstance(svc, module.TaskService)
    assert svc._api_base_url == "https://backend.example"


def test_task_module_wires_bbs_digest_report_service_singleton():
    injector = Injector(
        [TaskModule(), _StubDiscoverModule(), _StubBbsCronModule(), _StubBbsDigestModule()]
    )
    service = injector.get(ForumDigestReportServiceProtocol)
    assert service is injector.get(ForumDigestReportServiceProtocol)
