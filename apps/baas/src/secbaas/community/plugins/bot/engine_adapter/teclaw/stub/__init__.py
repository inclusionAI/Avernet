"""TeClaw adapter 测试桩(noop/mock)。"""

from ._stub_teclaw import MockTeClawAdapter, NoopTeClawAdapter

__all__ = ["MockTeClawAdapter", "NoopTeClawAdapter"]
