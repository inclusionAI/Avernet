"""TeClaw engine adapter plugin —— real 生产实现 + stub 测试桩(noop/mock)。"""

from .real import TeClawAdapter
from .stub import MockTeClawAdapter, NoopTeClawAdapter

__all__ = ["MockTeClawAdapter", "NoopTeClawAdapter", "TeClawAdapter"]
