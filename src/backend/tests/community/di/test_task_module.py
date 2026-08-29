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
from agentclaw.community.di.modules.task_module import TaskModule
from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
    SingleboxKeywordBotDiscover,
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


def test_resolve_api_base_url_uses_bcs_task_callback_origin(monkeypatch):
    # 回投 origin 取自 bcs_client.task_callback_url[_pre]。BCS client 已按环境选择，
    # TaskModule 只负责取 origin，社区测试使用中立域名。

    # singlebox → SINGLEBOX_BACKEND_URL(本地直连),不走 iframe 解析
    monkeypatch.setenv("DEPLOY_PROFILE", "singlebox")
    monkeypatch.delenv("SINGLEBOX_BACKEND_URL", raising=False)
    assert TaskModule._resolve_api_base_url("ignored") == "http://localhost:8888"
    monkeypatch.setenv("SINGLEBOX_BACKEND_URL", "http://sb.local:8888")
    assert TaskModule._resolve_api_base_url("ignored") == "http://sb.local:8888"

    # non-singlebox → 取 bcs_client.task_callback_url 的 origin(去路径)
    monkeypatch.setenv("DEPLOY_PROFILE", "community")
    assert TaskModule._resolve_api_base_url("") == "http://localhost:8888"          # 空 → 兜底
    assert TaskModule._resolve_api_base_url("not a url") == "http://localhost:8888"  # 非法 → 兜底
    # 真实形态:task_callback_url_pre 可以带 callback 路径，解析时只保留 origin
    assert (
        TaskModule._resolve_api_base_url(
            "https://agentclaw-pre.example.test/api/economy/governance/card-callback"
        )
        == "https://agentclaw-pre.example.test"
    )
    # 带端口/查询也应正确取 origin
    assert (
        TaskModule._resolve_api_base_url("https://be.local:8888/path?x=1")
        == "https://be.local:8888"
    )


def test_task_module_uses_keyword_discovery_in_every_profile(monkeypatch):
    class _BotPublic:
        def search_public_bots_by_keyword(self, **_kwargs):
            return {"total": 0, "items": []}

    for profile in ("singlebox", "corp", "community"):
        monkeypatch.setenv("DEPLOY_PROFILE", profile)
        discover = TaskModule._resolve_discover(bot_public=_BotPublic())
        assert isinstance(discover, SingleboxKeywordBotDiscover)
