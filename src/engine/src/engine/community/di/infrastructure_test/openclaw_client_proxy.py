"""Test OpenClaw /client WS proxy binding — unsupported like community."""
from __future__ import annotations

from engine.community.di.infrastructure.openclaw_client_proxy import CommunityOpenClawClientProxyModule


class TestOpenClawClientProxyModule(CommunityOpenClawClientProxyModule):
    """Test profile OpenClaw /client proxy uses community unsupported impl."""
