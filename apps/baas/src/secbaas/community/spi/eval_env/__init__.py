"""评测环境 SPI — BaaS 侧服务提供者接口。"""

from ._protocols import (
    EvalBindingResolverProtocol,
    EvalConsistencyCheckProtocol,
    EvalSessionLog,
)

__all__ = [
    "EvalBindingResolverProtocol",
    "EvalConsistencyCheckProtocol",
    "EvalSessionLog",
]
