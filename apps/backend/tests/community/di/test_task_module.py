from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator,
    NoopCallbackAuthenticator,
)
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry,
    InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.core.task.task_dispatch.claim_join_gate import (
    TaskSettingsServiceProtocol,
)
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


def test_callback_origin_uses_bcs_task_callback_url_and_ignores_paths():
    """TaskModule extracts the configured BCS callback origin without profile branches."""
    assert TaskModule._callback_origin("") == "http://localhost:8888"
    assert TaskModule._callback_origin("not a url") == "http://localhost:8888"
    assert TaskModule._callback_origin("http://local:8888") == "http://local:8888"
    assert (
        TaskModule._callback_origin(
            "https://agentclaw-pre.example.test/api/economy/governance/card-callback"
        )
        == "https://agentclaw-pre.example.test"
    )
    assert (
        TaskModule._callback_origin("https://be.local:8888/path?x=1")
        == "https://be.local:8888"
    )


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
        _Graph(), object(), TaskDispatchConfig(), _Injector()
    )
    assert isinstance(svc, module.TaskService)
