"""A2A v1.0 Agent Card — pure JSON generation.

Spec: card fields verified in spike G1a; tags is required (spec §4.4.5).
"""
from __future__ import annotations

from typing import Any

# Extension URIs —— 词汇表由 spike G2 定版、本文件为权威定义（spike 已随 PR 清理）
# 1c：spec v1.0 AgentCapabilities 无标准 cancellation 字段（capabilities 仅
# streaming/pushNotifications/extensions；CancelTask 本身是核心方法），服务
# 侧取消能力以 extension URI 锁定声明——BaaS 端读卡判读（灰度期老引擎
# 无此声明 → abort 诚实不回退假 ok）。
EXT_BASE = "https://avernet.local/a2a/ext"
# resolve = 词汇表条目 + 条件声明（P0/stage2 §5）：单一 URI 代表完整
# resolve 能力集，仅在活动引擎 ChatService 三方法全实现时才入卡
# （router._engine_resolve_supported 计算真值）；其余五项无条件声明。
EXTENSION_URIS = {
    "inject": f"{EXT_BASE}/inject/v1",
    "context": f"{EXT_BASE}/context/v1",
    "replay": f"{EXT_BASE}/replay/v1",
    "retry": f"{EXT_BASE}/retry/v1",
    "cancel": f"{EXT_BASE}/cancel/v1",
    "resolve": f"{EXT_BASE}/resolve/v1",
}


def make_agent_card(base_url: str, *,
                    agent_name: str = "OpenClaw Engine",
                    description: str = "Engine adapter with A2A v1.0 JSON-RPC binding",
                    version: str = "0.1.0",
                    resolve_supported: bool = False) -> dict[str, Any]:
    # 条件声明：非 resolve 五项恒出；resolve 仅在 resolve_supported=True
    # 时入卡（真值由调用方按活动引擎能力计算，请求时取、不缓存）。
    declared_uris = [u for k, u in EXTENSION_URIS.items()
                     if k != "resolve"] + (
        [EXTENSION_URIS["resolve"]] if resolve_supported else [])
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
                for uri in declared_uris
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
