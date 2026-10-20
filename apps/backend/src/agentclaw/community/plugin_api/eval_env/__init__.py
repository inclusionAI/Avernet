"""评测环境 Plugin API — 能力契约集合。

本包定义评测环境相关的 6 个 Plugin Protocol，遵循微内核架构
(Rule 3/5/20)：

- EvalEnvLifecycleProtocol — 评测环境生命周期（创建/销毁/连接）
- EvalEnvBuildLifecycle — 评测环境构建生命周期（B3 编排消费面：异步 create/轮询 build-status/destroy）
- EvalBindingResolverProtocol — 评测绑定解析
- EvalVersionSyncProtocol — 评测版本同步
- EvalTagPropagationProtocol — 评测标签传播
- EvalMcpGatewayProtocol — 评测 MCP 网关访问控制
- EvalBcsCliTagProtocol — 评测 BCS 出站标拦截注入
"""

from ._constants import DYNAMIC_ENV_TAG_KEY, HEADER_DEFAULT_TAG, HEADER_EVAL_ID
from ._eval_env_lifecycle import EvalEnvLifecycleProtocol
from ._eval_env_build_lifecycle import EvalEnvBuildLifecycle
from ._eval_binding_resolver import EvalBindingResolverProtocol
from ._eval_version_sync import EvalVersionSyncProtocol
from ._eval_tag_propagation import EvalTagPropagationProtocol
from ._eval_mcp_gateway import EvalMcpGatewayProtocol
from ._eval_bcs_cli_tag import EvalBcsCliTagProtocol

__all__ = [
    "DYNAMIC_ENV_TAG_KEY",
    "HEADER_DEFAULT_TAG",
    "HEADER_EVAL_ID",
    "EvalEnvLifecycleProtocol",
    "EvalEnvBuildLifecycle",
    "EvalBindingResolverProtocol",
    "EvalVersionSyncProtocol",
    "EvalTagPropagationProtocol",
    "EvalMcpGatewayProtocol",
    "EvalBcsCliTagProtocol",
]