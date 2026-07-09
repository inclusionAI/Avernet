"""Claude Code engine adapter plugin —— real 生产实现 + stub 测试桩(noop/mock)。"""

from .real import ClaudeCodeAdapter
from .stub import MockClaudeCodeAdapter, NoopClaudeCodeAdapter

__all__ = ["ClaudeCodeAdapter", "MockClaudeCodeAdapter", "NoopClaudeCodeAdapter"]
