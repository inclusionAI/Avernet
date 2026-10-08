"""原 ARCA 创建切 BaaS 支持的业务分支。"""

from __future__ import annotations


SUPPORTED_ARCA_CREATE_BAAS_ROLLOUT_BRANCHES = frozenset(
    {
        ("personal", "openclaw"),
        ("service", "openclaw"),
        ("personal", "claude_code"),
        ("service", "claude_code"),
        ("personal", "aicoding"),
        ("service", "aicoding"),
        ("personal", "hermes"),
    }
)
