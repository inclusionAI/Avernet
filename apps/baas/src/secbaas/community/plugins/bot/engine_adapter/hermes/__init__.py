"""Hermes engine adapter plugin —— real 生产实现 + stub 测试桩(noop/mock)。"""

from .real import HermesAdapter
from .stub import MockHermesAdapter, NoopHermesAdapter

__all__ = ["HermesAdapter", "MockHermesAdapter", "NoopHermesAdapter"]
