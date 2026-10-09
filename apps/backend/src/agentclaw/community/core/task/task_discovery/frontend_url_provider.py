"""FrontendUrlProvider: task_discovery 的前端 URL 取数端口(core 只含中性端口 + 空实现)。

Mirrors ``BcsBotTokenProvider`` (``community.core.task.task_runner.integration.bcs_bot_token_provider``)
and ``NotifyMessagesProvider`` (``community.core.task.task_discovery.notify_messages_provider``):

- community core/ 只定义 ``Protocol`` + ``NullFrontendUrlProvider`` 空实现;core 不读 YAML
  / env(解析在 DI factory/corp 实现完成),不持厂商部署差异。
- corp 由 ``CorpTaskIntegrationModule.get_frontend_url_provider`` 经 DI bind
  ``CorpFrontendUrlProvider`` — 构造期一次性按 ``get_current_env()`` 选
  ``frontend_url_pre``/``frontend_url_prod``/``frontend_url``(来自
  ``TaskDiscoveryDingTalkConfig``),``get()`` 时运行时 holder(``POST
  /discovery/dingtalk-config`` 热注入)优先于静态值,保持"运行时注入 > 配置"语义。
- 未注入时(community/singlebox/test 列,initiator 走 corp-only 装配路径才有 DI 解析)
  fallback ``NullFrontendUrlProvider``(返回空串,下游用构造兜底 localhost)。

``CorpFrontendUrlProvider`` 位于
``src/agentclaw/corp/di/modules/infrastructure/corp/corp_task_integration.py``。

运行时热注入(不重启 backend)仍由全局 ``FrontendUrlHolder``
(``session_initiator.py``)承载 —— holder 不在本 port 范围内删除:
``adapters/http/task/router.py::set_dingtalk_config`` 等旧 reader
(``CronRelaySessionInitiator`` / legacy ``session_creator``)继续读 holder。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class FrontendUrlProvider(Protocol):
    """``get() -> frontend_url``` 前端 workbench 地址取数端口。

    实现约定(priority 与 legacy ``FrontendUrlHolder`` holder 链一致):
      - 运行时注入(e2e/API ``POST /discovery/dingtalk-config``)若存在则优先;
      - 否则返回 env-aware 静态解析值(corp:YAML ``task_discovery_dingtalk``
        块按 env 选择);
      - 未配置返回空串(下游 ``OpenApiBotSessionInitiator`` 回落构造默认
        ``http://localhost:8000``)。

    使用方式::

        provider: FrontendUrlProvider = injector.get(FrontendUrlProvider)
        base = (provider.get() or "http://localhost:8000").rstrip("/")
    """

    def get(self) -> str: ...


class NullFrontendUrlProvider:
    """空实现(singlebox/test/未配置):恒返回空串,降级不阻断。

    Mirror ``NullBcsBotTokenProvider`` / ``NullNotifyMessagesProvider``:
    corp 列未注入 corp provider 或纯内核测试列时由 DI 侧兜底;
    下游拿到空串走构造默认值。**不**读运行时 holder(holder 属 legacy
    全局态;经本端口消费的新代码不应隐式依赖它)。
    """

    def get(self) -> str:
        return ""
