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
        device_id: PaaS device id of the selected device
            (e.g., "ARCA_SANDBOX-abc"). Empty string until the dispatcher
            backfills it on the success path.
        a2a_base_url: Authoritative HTTP base URL of the device's A2A agent
            face (card at ``{base}/.well-known/agent-card.json``, RPC at
            ``{base}/rpc``), addressed so that a *caller's browser* can reach
            it directly — not the in-cluster proxypass form ``ws_url`` takes.
            Empty string when the sandbox platform defines no such address
            (the additive default); the field then travels as absent, and
            consumers fall back to deriving whatever they could before.
    """

    ws_url: str
    token: str
    target: str
    expires_at: datetime
    device_id: str = ""
    a2a_base_url: str = ""
