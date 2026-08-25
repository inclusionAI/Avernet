"""评测环境 Noop 本地实现 — 评测功能关闭时使用。"""

from ._noop_lifecycle import NoopEvalEnvLifecycle
from ._noop_binding_resolver import NoopEvalBindingResolver
from ._noop_version_sync import NoopEvalVersionSync
from ._noop_tag_propagation import NoopEvalTagPropagation
from ._noop_mcp_gateway import NoopEvalMcpGateway
from ._noop_bcs_cli_tag import NoopEvalBcsCliTag

__all__ = [
    "NoopEvalEnvLifecycle",
    "NoopEvalBindingResolver",
    "NoopEvalVersionSync",
    "NoopEvalTagPropagation",
    "NoopEvalMcpGateway",
    "NoopEvalBcsCliTag",
]