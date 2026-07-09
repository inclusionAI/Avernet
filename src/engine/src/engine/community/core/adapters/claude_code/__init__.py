"""ClaudeCode ACL adapters — core *Service implementations over native ports.

Each adapter implements one core ``*Service`` Protocol by delegating to an
injected ``ClaudeCode*Port`` (from ``engine.community.plugin_api.claude_code``) and
translating between core DTOs and the port's native dict / list / bool shapes.
"""
from __future__ import annotations

from engine.community.core.adapters.claude_code.chat import ClaudeCodeChatAdapter
from engine.community.core.adapters.claude_code.cron import ClaudeCodeCronAdapter
from engine.community.core.adapters.claude_code.file import ClaudeCodeFileAdapter
from engine.community.core.adapters.claude_code.mcp import ClaudeCodeMcpAdapter
from engine.community.core.adapters.claude_code.models import ClaudeCodeModelsAdapter
from engine.community.core.adapters.claude_code.relay import ClaudeCodeRelayAdapter
from engine.community.core.adapters.claude_code.session import ClaudeCodeSessionAdapter
from engine.community.core.adapters.claude_code.skills import ClaudeCodeSkillsAdapter

__all__ = [
    "ClaudeCodeChatAdapter",
    "ClaudeCodeCronAdapter",
    "ClaudeCodeFileAdapter",
    "ClaudeCodeMcpAdapter",
    "ClaudeCodeModelsAdapter",
    "ClaudeCodeRelayAdapter",
    "ClaudeCodeSessionAdapter",
    "ClaudeCodeSkillsAdapter",
]
