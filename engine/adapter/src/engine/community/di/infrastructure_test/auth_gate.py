from __future__ import annotations

from engine.community.di.infrastructure.auth_gate import CommunityAuthGateModule


class TestAuthGateModule(CommunityAuthGateModule):
    """Test auth gate currently uses deterministic allow-all no-op."""
