"""
OpenClaw Gateway protocol — handshake types + frame parser.

Wire envelope types (RequestFrame, ResponseFrame, EventFrame, ErrorShape,
ErrorCodes, constants) live in `engine.community.kernel.frames` because they're shared
across engines. The types in this module — ClientInfo, HelloOk, etc. — are
specific to the OpenClaw gateway's connect handshake and are not universal;
other engines (AiCoding, future engines) may use different handshake
formats.

Historically these types lived in `engine.moltis.client.protocol` because
OpenClaw was bolted on top of the Moltis protocol. Moltis has been removed,
so the handshake types now live alongside the OpenClaw client/server that
use them.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

# Re-export wire frames for convenience — OpenClaw callers can import everything
# they need from a single module.
from engine.community.kernel.frames import (
    HANDSHAKE_TIMEOUT_MS,
    MAX_BUFFERED_BYTES,
    MAX_PAYLOAD_BYTES,
    PROTOCOL_VERSION,
    TICK_INTERVAL_MS,
    ErrorCodes,
    ErrorShape,
    EventFrame,
    RequestFrame,
    ResponseFrame,
    StateVersion,
)


def _openclaw_gateway_protocol_version() -> int:
    raw = os.getenv("OPENCLAW_GATEWAY_PROTOCOL_VERSION", "4").strip()
    try:
        return int(raw)
    except ValueError:
        return 4


# Keep this separate from PROTOCOL_VERSION: the adapter's inbound websocket
# protocol remains v3 while OpenClaw Gateway 2026.5.x requires v4 during its
# connect handshake.
OPENCLAW_GATEWAY_PROTOCOL_VERSION = _openclaw_gateway_protocol_version()


# ── Handshake types ─────────────────────────────────────────────────────────


@dataclass
class ClientInfo:
    """Client info sent during connect."""

    id: str
    version: str
    platform: str
    mode: str
    display_name: str | None = None
    device_family: str | None = None
    model_identifier: str | None = None
    instance_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "platform": self.platform,
            "mode": self.mode,
        }
        if self.display_name is not None:
            d["displayName"] = self.display_name
        if self.device_family is not None:
            d["deviceFamily"] = self.device_family
        if self.model_identifier is not None:
            d["modelIdentifier"] = self.model_identifier
        if self.instance_id is not None:
            d["instanceId"] = self.instance_id
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClientInfo:
        return cls(
            id=data["id"],
            version=data["version"],
            platform=data["platform"],
            mode=data["mode"],
            display_name=data.get("displayName"),
            device_family=data.get("deviceFamily"),
            model_identifier=data.get("modelIdentifier"),
            instance_id=data.get("instanceId"),
        )


@dataclass
class ConnectAuth:
    """Authentication info sent during connect."""

    token: str | None = None
    password: str | None = None
    api_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.token is not None:
            d["token"] = self.token
        if self.password is not None:
            d["password"] = self.password
        if self.api_key is not None:
            d["api_key"] = self.api_key
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectAuth:
        return cls(
            token=data.get("token"),
            password=data.get("password"),
            api_key=data.get("api_key"),
        )


@dataclass
class DeviceIdentity:
    """Device identity (Ed25519 signature auth)."""

    id: str
    public_key: str
    signature: str
    signed_at: int
    nonce: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "publicKey": self.public_key,
            "signature": self.signature,
            "signedAt": self.signed_at,
            "nonce": self.nonce,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeviceIdentity:
        return cls(
            id=data["id"],
            public_key=data["publicKey"],
            signature=data["signature"],
            signed_at=data["signedAt"],
            nonce=data["nonce"],
        )


@dataclass
class ConnectParams:
    """Connect-request parameters."""

    min_protocol: int
    max_protocol: int
    client: ClientInfo
    caps: list[str] | None = None
    commands: list[str] | None = None
    permissions: dict[str, Any] | None = None
    path_env: str | None = None
    role: str | None = None
    scopes: list[str] | None = None
    auth: ConnectAuth | None = None
    locale: str | None = None
    user_agent: str | None = None
    timezone: str | None = None
    device: DeviceIdentity | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "minProtocol": self.min_protocol,
            "maxProtocol": self.max_protocol,
            "client": self.client.to_dict(),
        }
        if self.caps is not None:
            d["caps"] = self.caps
        if self.commands is not None:
            d["commands"] = self.commands
        if self.permissions is not None:
            d["permissions"] = self.permissions
        if self.path_env is not None:
            d["pathEnv"] = self.path_env
        if self.role is not None:
            d["role"] = self.role
        if self.scopes is not None:
            d["scopes"] = self.scopes
        if self.auth is not None:
            d["auth"] = self.auth.to_dict()
        if self.locale is not None:
            d["locale"] = self.locale
        if self.user_agent is not None:
            d["userAgent"] = self.user_agent
        if self.timezone is not None:
            d["timezone"] = self.timezone
        if self.device is not None:
            d["device"] = self.device.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectParams:
        auth = None
        if data.get("auth"):
            auth = ConnectAuth.from_dict(data["auth"])
        device = None
        if data.get("device"):
            device = DeviceIdentity.from_dict(data["device"])
        return cls(
            min_protocol=data["minProtocol"],
            max_protocol=data["maxProtocol"],
            client=ClientInfo.from_dict(data["client"]),
            caps=data.get("caps"),
            commands=data.get("commands"),
            permissions=data.get("permissions"),
            path_env=data.get("pathEnv"),
            role=data.get("role"),
            scopes=data.get("scopes"),
            auth=auth,
            locale=data.get("locale"),
            user_agent=data.get("userAgent"),
            timezone=data.get("timezone"),
            device=device,
        )


@dataclass
class ServerInfo:
    """Server info returned in HelloOk."""

    version: str
    conn_id: str
    commit: str | None = None
    host: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"version": self.version, "connId": self.conn_id}
        if self.commit is not None:
            d["commit"] = self.commit
        if self.host is not None:
            d["host"] = self.host
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ServerInfo:
        return cls(
            version=data["version"],
            conn_id=data["connId"],
            commit=data.get("commit"),
            host=data.get("host"),
        )


@dataclass
class Features:
    """Advertised server features."""

    methods: list[str]
    events: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"methods": self.methods, "events": self.events}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Features:
        return cls(
            methods=data.get("methods", []),
            events=data.get("events", []),
        )


@dataclass
class HelloAuth:
    """Auth block in HelloOk."""

    device_token: str
    role: str
    scopes: list[str]
    issued_at_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "deviceToken": self.device_token,
            "role": self.role,
            "scopes": self.scopes,
        }
        if self.issued_at_ms is not None:
            d["issuedAtMs"] = self.issued_at_ms
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HelloAuth:
        return cls(
            device_token=data.get("deviceToken", ""),
            role=data.get("role", "operator"),
            scopes=data.get("scopes", []),
            issued_at_ms=data.get("issuedAtMs"),
        )


@dataclass
class Policy:
    """Connection policy limits."""

    max_payload: int = MAX_PAYLOAD_BYTES
    max_buffered_bytes: int = MAX_BUFFERED_BYTES
    tick_interval_ms: int = TICK_INTERVAL_MS

    def to_dict(self) -> dict[str, Any]:
        return {
            "maxPayload": self.max_payload,
            "maxBufferedBytes": self.max_buffered_bytes,
            "tickIntervalMs": self.tick_interval_ms,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Policy:
        return cls(
            max_payload=data.get("maxPayload", MAX_PAYLOAD_BYTES),
            max_buffered_bytes=data.get("maxBufferedBytes", MAX_BUFFERED_BYTES),
            tick_interval_ms=data.get("tickIntervalMs", TICK_INTERVAL_MS),
        )


@dataclass
class HelloOk:
    """Successful handshake response."""

    protocol: int
    server: ServerInfo
    features: Features
    policy: Policy
    snapshot: Any = field(default_factory=dict)
    canvas_host_url: str | None = None
    auth: HelloAuth | None = None
    type: str = "hello-ok"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": self.type,
            "protocol": self.protocol,
            "server": self.server.to_dict(),
            "features": self.features.to_dict(),
            "snapshot": self.snapshot,
            "policy": self.policy.to_dict(),
        }
        if self.canvas_host_url is not None:
            d["canvasHostUrl"] = self.canvas_host_url
        if self.auth is not None:
            d["auth"] = self.auth.to_dict()
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HelloOk:
        auth = None
        if data.get("auth"):
            auth = HelloAuth.from_dict(data["auth"])
        return cls(
            protocol=data["protocol"],
            server=ServerInfo.from_dict(data["server"]),
            features=Features.from_dict(data["features"]),
            policy=Policy.from_dict(data.get("policy", {})),
            snapshot=data.get("snapshot", {}),
            canvas_host_url=data.get("canvasHostUrl"),
            auth=auth,
            type=data.get("type", "hello-ok"),
        )


# ── Roles and scopes ────────────────────────────────────────────────────────


class Roles:
    OPERATOR = "operator"
    NODE = "node"


class Scopes:
    ADMIN = "operator.admin"
    READ = "operator.read"
    WRITE = "operator.write"
    APPROVALS = "operator.approvals"
    PAIRING = "operator.pairing"


# ── Frame parser ────────────────────────────────────────────────────────────


def parse_frame(data: str) -> RequestFrame | ResponseFrame | EventFrame:
    """Parse a raw JSON string into the appropriate frame type."""
    obj = json.loads(data)
    frame_type = obj.get("type")
    if frame_type == "req":
        return RequestFrame.from_dict(obj)
    elif frame_type == "res":
        return ResponseFrame.from_dict(obj)
    elif frame_type == "event":
        return EventFrame.from_dict(obj)
    else:
        raise ValueError(f"Unknown frame type: {frame_type}")


__all__ = [
    # Re-exported wire types
    "PROTOCOL_VERSION",
    "OPENCLAW_GATEWAY_PROTOCOL_VERSION",
    "MAX_PAYLOAD_BYTES",
    "MAX_BUFFERED_BYTES",
    "TICK_INTERVAL_MS",
    "HANDSHAKE_TIMEOUT_MS",
    "ErrorCodes",
    "ErrorShape",
    "RequestFrame",
    "ResponseFrame",
    "EventFrame",
    "StateVersion",
    # Handshake types
    "ClientInfo",
    "ConnectAuth",
    "DeviceIdentity",
    "ConnectParams",
    "ServerInfo",
    "Features",
    "HelloAuth",
    "Policy",
    "HelloOk",
    "Roles",
    "Scopes",
    # Parser
    "parse_frame",
]
