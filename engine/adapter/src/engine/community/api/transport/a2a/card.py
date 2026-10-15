"""A2A v1.0 Agent Card — pure JSON generation.

Spec: card fields verified in spike G1a; tags is required (spec §4.4.5).
"""
from __future__ import annotations

from typing import Any

# Extension URIs (G2 locked — spike/a2a/settings.py EXT_URIS same origin)
# 1c：spec v1.0 AgentCapabilities 无标准 cancellation 字段（capabilities 仅
# streaming/pushNotifications/extensions；CancelTask 本身是核心方法），服务
# 侧取消能力以 extension URI 锁定声明——BaaS 端读卡判读（灰度期老引擎
# 无此声明 → abort 诚实不回退假 ok）。
EXT_BASE = "https://avernet.local/a2a/ext"
EXTENSION_URIS = {
    "inject": f"{EXT_BASE}/inject/v1",
    "context": f"{EXT_BASE}/context/v1",
    "replay": f"{EXT_BASE}/replay/v1",
    "retry": f"{EXT_BASE}/retry/v1",
    "cancel": f"{EXT_BASE}/cancel/v1",
}


def make_agent_card(base_url: str, *,
                    agent_name: str = "OpenClaw Engine",
                    description: str = "Engine adapter with A2A v1.0 JSON-RPC binding",
                    version: str = "0.1.0") -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": agent_name,
        "description": description,
        "version": version,
        "supportedInterfaces": [
            {
                "url": f"{base_url}/rpc",
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0",
            },
        ],
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "extensions": [
                {"url": uri, "required": False}
                for uri in EXTENSION_URIS.values()
            ],
        },
        "skills": [
            {
                "id": "conversation",
                "name": "Conversation",
                "description": "Send messages and receive streamed responses",
                "tags": ["conversation"],
            },
        ],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
    }
    return out
