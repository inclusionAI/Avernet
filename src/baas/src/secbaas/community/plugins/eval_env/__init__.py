"""评测环境 Plugin — BaaS 侧 real/stub 实现。"""

from .real import (
    RealEvalBindingResolver,
    RealEvalConsistencyCheck,
    RealEvalSessionLog,
)
from .stub import (
    NoopEvalBindingResolver,
    NoopEvalConsistencyCheck,
    NoopEvalSessionLog,
)

__all__ = [
    "RealEvalBindingResolver",
    "RealEvalConsistencyCheck",
    "RealEvalSessionLog",
    "NoopEvalBindingResolver",
    "NoopEvalConsistencyCheck",
    "NoopEvalSessionLog",
]
