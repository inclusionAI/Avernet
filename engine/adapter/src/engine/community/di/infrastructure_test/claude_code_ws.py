"""Test claude_code WS binding — reuses the community generic server."""
from __future__ import annotations

from engine.community.di.infrastructure.claude_code_ws import (
    CommunityClaudeCodeWsModule,
)


class TestClaudeCodeWsModule(CommunityClaudeCodeWsModule):
    """Test profile claude_code /ws uses the community generic server."""
