from dataclasses import dataclass
from datetime import datetime


@dataclass
class WsConnectionInfo:
    """WebSocket connection information for direct device access.

    This dataclass contains all information needed for a caller to establish
    a direct WebSocket connection to a device via the agentclawproxy gateway.

    Attributes:
        ws_url: Full WebSocket URL (wss://gateway/proxypass/target/api/openclaw/ws)
        token: JWT proxypass token for authentication at the gateway
        target: Target identifier (format: ARCA_{sandbox_id}:{port})
        expires_at: Token expiration timestamp (UTC)
    """

    ws_url: str
    token: str
    target: str
    expires_at: datetime
