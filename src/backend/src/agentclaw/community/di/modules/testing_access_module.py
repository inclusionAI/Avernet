"""TestingAccessModule — LOCAL runtime overrides for the access module.

singlebox 本机开发模式下 ``PolicyServiceProtocol`` 解析为 ``LocalPolicyService``
(``check`` 始终 True,singlebox 是单用户本机环境,无白名单 + 抢配额业务);
pytest / CI 维持真 ``PolicyService`` (让 e2e access 流程 + endpoint 单测能验
真白名单 / quota 业务逻辑)。

加载时机: 由 ``modules_for(profile)`` 在 ``test`` / ``singlebox`` profile 下挂载,
layer 在 ``AccessModule`` (prod 默认,base list) 之上;后挂的 binding 赢。

分流姿势: ``SERVER_ENV=singlebox`` → ``LocalPolicyService``; 否则 → 注入回真
``PolicyService`` (即 ``AccessModule`` 已经绑好的 Protocol alias)。``SERVER_ENV``
是显式声明的运行模式 (config-driven wiring,符合 arch Rule 14),不是隐式 sys.modules
探针; pytest 跑时默认不带 ``SERVER_ENV=singlebox``,自动落回真 ``PolicyService``,
e2e access 测试不受影响。

与 ``AccessModule`` 的关系: prod ``AccessModule`` 仍把 ``PolicyService`` 绑为
``PolicyServiceProtocol`` 的 alias;本 module 在 singlebox 时 override,在 pytest
时 (delegate 到真 ``PolicyService``) 等价于不 override。``UserService`` /
``PolicyRepository`` / ``UnifiedPolicyRepository`` 不受影响 — 它们也都是
mode-agnostic,singlebox 的 SQLite 也能正常跑 (只是 ``LocalPolicyService`` 根本
不会去查它们)。
"""
from __future__ import annotations

import os

from injector import Module, inject, provider, singleton

from agentclaw.community.api.policy_service import PolicyServiceProtocol
from agentclaw.community.core.access.services.policy_service import PolicyService
from agentclaw.community.log import get_logger
from agentclaw.community.plugins.local.policy_service import LocalPolicyService

logger = get_logger()


class TestingAccessModule(Module):
    """singlebox 本机的 ``PolicyServiceProtocol`` 覆盖; pytest 时透传真 impl。"""

    @singleton
    @provider
    @inject
    def _policy_service_protocol(
        self, prod_impl: PolicyService
    ) -> PolicyServiceProtocol:
        """SERVER_ENV=singlebox → LocalPolicyService; 否则 → 真 PolicyService。

        pytest 进程默认不设 ``SERVER_ENV=singlebox``,所以 e2e access 测试 +
        endpoint runner 测试拿到真 ``PolicyService``,业务逻辑可被验证。
        singlebox 启动时 ``backend.sh`` 显式 ``SERVER_ENV=singlebox``,拿
        ``LocalPolicyService`` 全开放,绕过 backend.db 空白名单导致的"仅对大安全
        团队开放"拦截。
        """
        if (os.getenv("SERVER_ENV") or "").lower() == "singlebox":
            logger.info(
                "[NEW-ARCH] PolicyServiceProtocol: LocalPolicyService (singlebox)"
            )
            return LocalPolicyService()
        logger.info(
            "[NEW-ARCH] PolicyServiceProtocol: PolicyService (pytest / CI)"
        )
        return prod_impl
