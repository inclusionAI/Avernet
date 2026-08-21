"""评测环境 SPI Protocol — BaaS 侧服务提供者接口。

re-export api 层 Protocol 供 SPI 消费者使用。
"""

from secbaas.community.api.eval_env import (
    EvalBindingResolverProtocol,
    EvalConsistencyCheckProtocol,
    EvalSessionLogProtocol,
)

__all__ = [
    "EvalBindingResolverProtocol",
    "EvalConsistencyCheckProtocol",
    "EvalSessionLogProtocol",
]
