from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.task.auth import (
    CallbackAuthenticator, NoopCallbackAuthenticator,
)
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.api.task.task_service import TaskServiceProtocol
from agentclaw.community.core.task.task_runner.callback_correlation import (
    CallbackCorrelationRegistry, InMemoryCallbackCorrelationRegistry,
)
from agentclaw.community.di.config import TaskCallbackConfig
from agentclaw.community.di.modules.task_module import TaskModule


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
            def search_public_bots_by_keyword(self, **kw):
                return {"total": 0, "items": []}
        return _BP()  # type: ignore[return-value]


def test_task_module_binds_callback_singletons():
    inj = Injector([TaskModule(), _StubDiscoverModule()])
    # 新增 callback 服务绑定
    assert isinstance(inj.get(CallbackCorrelationRegistry), InMemoryCallbackCorrelationRegistry)
    assert isinstance(inj.get(CallbackAuthenticator), NoopCallbackAuthenticator)
    # 既有 TaskServiceProtocol 仍可解析(端口接线未被破坏)
    assert isinstance(inj.get(TaskServiceProtocol).__class__.__name__, str)
    # singleton:两次取同对象
    assert inj.get(TaskServiceProtocol) is inj.get(TaskServiceProtocol)
    assert inj.get(CallbackCorrelationRegistry) is inj.get(CallbackCorrelationRegistry)
    assert inj.get(CallbackAuthenticator) is inj.get(CallbackAuthenticator)


def test_resolve_ports_outside_singlebox_returns_the_two_port_contract(monkeypatch):
    monkeypatch.setenv("DEPLOY_PROFILE", "community")

    assert TaskModule._resolve_ports() == (None, None)


def test_resolve_api_base_url_uses_neutral_config_default(monkeypatch):
    monkeypatch.setenv("DEPLOY_PROFILE", "community")
    # 非单 box:回投 origin 取 TaskCallbackConfig(get_current_env 选 _pre/base);空/未绑定 → 回退 localhost
    monkeypatch.setenv("SERVER_ENV", "prod")  # get_current_env() -> "prod"
    assert TaskModule._resolve_api_base_url(None) == "http://localhost:8888"
    assert TaskModule._resolve_api_base_url(TaskCallbackConfig()) == "http://localhost:8888"
    # prod -> base_url
    assert (
        TaskModule._resolve_api_base_url(TaskCallbackConfig(
            base_url="https://teamclawgw.alipay.com",
            base_url_pre="https://teamclawgw-pre.alipay.com",
        )) == "https://teamclawgw.alipay.com"
    )
    monkeypatch.setenv("SERVER_ENV", "pre")  # get_current_env() -> "pre"
    # pre -> base_url_pre override
    assert (
        TaskModule._resolve_api_base_url(TaskCallbackConfig(
            base_url="https://teamclawgw.alipay.com",
            base_url_pre="https://teamclawgw-pre.alipay.com",
        )) == "https://teamclawgw-pre.alipay.com"
    )
