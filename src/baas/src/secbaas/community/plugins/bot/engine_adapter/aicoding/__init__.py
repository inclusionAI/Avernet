"""AICoding engine adapter plugin —— real 生产实现 + stub 测试桩(noop/mock)。"""

from .real import AICodingAdapter
from .stub import MockAICodingAdapter, NoopAICodingAdapter

__all__ = ["AICodingAdapter", "MockAICodingAdapter", "NoopAICodingAdapter"]
