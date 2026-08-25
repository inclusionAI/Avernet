"""TestEvalEnvModule — 评测环境 Plugin DI 绑定（Noop 模式）。

在 TEST / SINGLEBOX / CORP_TEST profile 下绑定 Noop 实现，
评测功能关闭。
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
from agentclaw.community.plugins.local.eval_env import (
    NoopEvalBcsCliTag,
    NoopEvalBindingResolver,
    NoopEvalEnvLifecycle,
    NoopEvalMcpGateway,
    NoopEvalTagPropagation,
    NoopEvalVersionSync,
)


class TestEvalEnvModule(Module):
    """测试环境评测 Plugin DI 模块 — 绑定 Noop 实现。

    评测功能关闭，所有 Protocol 调用走 Noop 降级路径。
    """

    def configure(self, binder: Binder) -> None:
        binder.bind(EvalEnvLifecycleProtocol, to=NoopEvalEnvLifecycle, scope=singleton)
        binder.bind(EvalBindingResolverProtocol, to=NoopEvalBindingResolver, scope=singleton)
        binder.bind(EvalVersionSyncProtocol, to=NoopEvalVersionSync, scope=singleton)
        binder.bind(EvalTagPropagationProtocol, to=NoopEvalTagPropagation, scope=singleton)
        binder.bind(EvalMcpGatewayProtocol, to=NoopEvalMcpGateway, scope=singleton)
        binder.bind(EvalBcsCliTagProtocol, to=NoopEvalBcsCliTag, scope=singleton)