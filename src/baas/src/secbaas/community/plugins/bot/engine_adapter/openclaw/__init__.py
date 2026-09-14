"""OpenClaw engine adapter plugin —— real 生产实现 + stub 测试桩(noop/mock)。"""

from .real import OpenClawAdapter
from .stub import MockOpenClawAdapter, NoopOpenClawAdapter

__all__ = ["MockOpenClawAdapter", "NoopOpenClawAdapter", "OpenClawAdapter"]
