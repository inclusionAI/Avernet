"""CommunityEvalEnvModule — 评测环境 Plugin DI 绑定（Prod 模式）。

在 CORP profile 下绑定 Prod 实现。
"""

from injector import Binder, Module, provider, singleton

from agentclaw.community.plugin_api.eval_env import (
    EvalBcsCliTagProtocol,
    EvalBindingResolverProtocol,
    EvalEnvLifecycleProtocol,
    EvalMcpGatewayProtocol,
    EvalTagPropagationProtocol,
    EvalVersionSyncProtocol,
)


class CommunityEvalEnvModule(Module):
    """Community 评测环境 Plugin DI 模块 — 绑定 Prod 实现。

    各 Prod 实现从 corp 侧注册表自动加载（modules_bootstrap），
    此处提供统一的 Protocol→Impl 绑定点。
    """

    def configure(self, binder: Binder) -> None:
        # Prod 实现在 corp/plugins/eval_env/prod/ 中，
        # 通过 modules_bootstrap 注册表由 CORP profile 加载。
        # 此处留空，corp 侧的 EvalEnvModule 负责具体绑定。
        pass