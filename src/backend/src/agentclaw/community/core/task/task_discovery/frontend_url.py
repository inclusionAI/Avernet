"""task_discovery 前端 URL 解析 — 中性配置模块（非 plugin，数据驱动）。

FrontendUrlProvider 从「plugin 契约 + 分列实现」降级为「配置数据 + 单一实现」：
取 URL 的算法两端完全相同（env 三选一的静态值 + 运行时 holder 热注入优先），
差异只是**数据来源**（community: user_config.task_discovery 中性块;
corp: task_discovery_dingtalk 块），因此用「DI 注入配置 + 同一个实现类」承载，
不需要 Protocol/@plugin_impl/分列实现这套插件机制。参照 ``CommunityCache``
的 ``redis_url`` 模式：配置存在与否只改变取值，不改变行为。

分层合规：
- 本模块在 core/，不读 YAML/env（架构规则禁止 core 读 user_config）；
  ``FrontendUrlConfig`` 由 DI 层（组合根）构造并注入。
- env 解析（pre/prod 选值）也是部署级常量,由组合根在装配期调用
  ``resolve_static_frontend_url`` 一次性完成（对齐原 CorpFrontendUrlProvider
  的 ctor 期固化语义）。

运行时热注入：``get()`` 优先读 ``FrontendUrlHolder``
（``adapters/http/task/router.py`` 的 ``POST /discovery/dingtalk-config`` 写入），
未注入（空串）回落 static 值。原 ``NullFrontendUrlProvider``「恒空串」的语义
由 ``ConfigFrontendUrlProvider()``（static=""）天然承载;原 Null 不读 holder 的
差异被统一为「holder 若被 e2e/运行时注入则优先」的一致语义。
"""
from __future__ import annotations

from dataclasses import dataclass

from agentclaw.community.log import get_logger

logger = get_logger()


class FrontendUrlHolder:
    """Runtime frontend URL override shared by task discovery integrations.

    2026-09-15 统一化重构时自 ``session_initiator.py`` 迁入（原文件因删除
    ``CronRelaySessionInitiator`` 而重写）。legacy runtime 热注入兼容:
    ``adapters/http/task/router.py`` 的 ``POST /discovery/dingtalk-config``
    与 corp 兼容路径写入本 holder,不属于 Plugin 契约。
    """

    _url: str = ""

    @classmethod
    def set(cls, url: str) -> None:
        cls._url = url.rstrip("/")
        logger.info("[FrontendUrlHolder] frontend url injected at runtime: %s", cls._url)

    @classmethod
    def get(cls) -> str:
        return cls._url


@dataclass(frozen=True)
class FrontendUrlConfig:
    """前端 workbench URL 的中性三档配置（env-aware 字段，DI 层装配）。

    来源（组合根职责，core 只收数据）:
    - community 列: ``user_config.task_discovery`` 块的
      ``frontend_url`` / ``frontend_url_pre`` / ``frontend_url_prod``。
    - corp 列: ``task_discovery_dingtalk`` 块的同名字段（既有 YAML 不动）。
    - test/local 列: 默认空（下游回落构造默认 localhost）。
    """

    url: str = ""
    url_pre: str = ""
    url_prod: str = ""


def resolve_static_frontend_url(cfg: FrontendUrlConfig, env: str) -> str:
    """按部署 env 解析静态 URL（pre→url_pre||url, prod→url_prod||url, else→url）。

    部署级常量，组合根在装配期调用一次并固化到 provider;``env`` 取
    ``get_current_env()``（prod|gray→prod / pre|prepub→pre 的两桶归一值）。
    """
    if env == "pre":
        return cfg.url_pre or cfg.url
    if env == "prod":
        return cfg.url_prod or cfg.url
    return cfg.url


class ConfigFrontendUrlProvider:
    """前端 workbench URL 取数实现（普通类: 持装配期固化的 static 值）。

    ``get()`` 语义: 运行时 ``FrontendUrlHolder`` 注入值优先,未注入回落 static,
    均空返回空串（下游 ``_build_session_url`` 回落构造默认
    ``http://localhost:8000``）。消费方按结构化鸭子类型使用（有 ``get()`` 即可）,
    不再要求继承任何 Protocol。
    """

    def __init__(self, static_url: str = "") -> None:
        self._static = static_url

    def get(self) -> str:
        return FrontendUrlHolder.get() or self._static


__all__ = [
    "ConfigFrontendUrlProvider",
    "FrontendUrlConfig",
    "resolve_static_frontend_url",
]