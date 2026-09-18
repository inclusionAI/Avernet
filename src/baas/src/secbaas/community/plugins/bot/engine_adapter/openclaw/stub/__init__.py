"""OpenClaw adapter 测试桩(noop/mock)。"""

from ._stub_openclaw import MockOpenClawAdapter, NoopOpenClawAdapter

__all__ = ["MockOpenClawAdapter", "NoopOpenClawAdapter"]
