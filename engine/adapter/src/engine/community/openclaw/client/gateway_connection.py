"""OpenClaw socket lifecycle and device-authentication handshake."""
from __future__ import annotations
import asyncio
import base64
import hashlib
import json
import logging
import ssl
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional
import websockets
from websockets.asyncio.client import ClientConnection
from engine.community.openclaw.protocol import (
    ClientInfo,
    ConnectAuth,
    ConnectParams,
    DeviceIdentity,
    EventFrame,
    HelloOk,
    OPENCLAW_GATEWAY_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    RequestFrame,
    ResponseFrame,
)
from engine.community.openclaw.config import OpenClawConfig, get_config

log = logging.getLogger("openclaw-client")

DEFAULT_DEVICE_KEY_FILE = Path.home() / ".openclaw" / "enterprise_device_key.pem"


def _normalize_base64_url(data: bytes) -> str:
    """将字节转换为 Base64URL 编码（无填充）"""
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _derive_device_id(public_key_bytes: bytes) -> str:
    """从公钥派生设备 ID（SHA-256 完整 hexdigest，64 字符）"""
    return hashlib.sha256(public_key_bytes).hexdigest()


def _build_device_auth_payload_v3(
    device_id: str,
    client_id: str,
    client_mode: str,
    role: str,
    scopes: list[str],
    signed_at_ms: int,
    token: Optional[str],
    nonce: str,
    platform: Optional[str],
    device_family: Optional[str],
) -> str:
    """构建 v3 签名 payload

    格式: v3|deviceId|clientId|clientMode|role|scopes|signedAt|token|nonce|platform|deviceFamily
    """
    scopes_str = ",".join(scopes)
    token_str = token or ""
    platform_str = platform or ""
    device_family_str = device_family or ""
    return "|".join([
        "v3",
        device_id,
        client_id,
        client_mode,
        role,
        scopes_str,
        str(signed_at_ms),
        token_str,
        nonce,
        platform_str,
        device_family_str,
    ])


def _load_or_create_device_keypair(
    key_file: Path = DEFAULT_DEVICE_KEY_FILE,
) -> tuple[Any, str, str]:
    """加载或创建设备密钥对

    返回: (private_key, public_key_base64url, device_id)
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        raise ImportError(
            "设备签名需要 'cryptography' 库。请安装: pip install cryptography"
        )

    key_file.parent.mkdir(parents=True, exist_ok=True)

    if key_file.exists():
        try:
            with open(key_file, "rb") as f:
                private_key = serialization.load_pem_private_key(f.read(), password=None)
            if not isinstance(private_key, Ed25519PrivateKey):
                raise ValueError("Invalid key type")
            pub_bytes = private_key.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            public_key_b64 = _normalize_base64_url(pub_bytes)
            device_id = _derive_device_id(pub_bytes)
            log.info(f"[device] 已加载设备密钥: device_id={device_id}")
            return private_key, public_key_b64, device_id
        except Exception as e:
            log.warning(f"[device] 加载密钥失败，将重新生成: {e}")

    # 生成新密钥对
    private_key = Ed25519PrivateKey.generate()
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    public_key_b64 = _normalize_base64_url(pub_bytes)
    device_id = _derive_device_id(pub_bytes)

    # 保存私钥
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_file.write_bytes(pem)
    key_file.chmod(0o600)  # 仅所有者可读写
    log.info(f"[device] 已生成并保存设备密钥: device_id={device_id}, path={key_file}")

    return private_key, public_key_b64, device_id


def _sign_payload(private_key: Any, payload: str) -> str:
    """签名 payload，返回 Base64URL 编码的签名"""
    signature = private_key.sign(payload.encode("utf-8"))
    return _normalize_base64_url(signature)


class GatewayConnection:
    """OpenClaw Gateway WebSocket 客户端"""

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        gateway_token: str | None = None,
        config: Optional[OpenClawConfig] = None,
        device_key_file: Optional[Path] = None,
        upstream_headers: Optional[Dict[str, str]] = None,
    ):
        if config:
            self._config = config
        else:
            self._config = get_config()
            if gateway_url:
                self._config.gateway_url = gateway_url
            if gateway_token:
                self._config.gateway_token = gateway_token

        self._ws: Optional[ClientConnection] = None
        self._hello: Optional[HelloOk] = None
        self._connected = False
        self._closing = False
        self._connection_lock = asyncio.Lock()
        # 统一透传 header（由 server 按配置开关决定是否传入）
        self._upstream_headers = dict(upstream_headers or {})

        # 请求响应匹配
        self._pending_requests: Dict[str, asyncio.Future] = {}

        # 事件监听器
        self._event_listeners: Dict[str, list[Callable[[EventFrame], None]]] = {}

        # 消息接收任务
        self._recv_task: Optional[asyncio.Task] = None

        # 序列号
        self._seq = 0

        # 设备密钥（延迟加载）
        self._device_key_file = device_key_file or DEFAULT_DEVICE_KEY_FILE
        self._device_private_key: Any | None = None
        self._device_public_key_b64: Optional[str] = None
        self._device_id: Optional[str] = None

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None

    @property
    def hello(self) -> Optional[HelloOk]:
        return self._hello

    def _next_id(self) -> str:
        return uuid.uuid4().hex[:8]

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def connect(self) -> HelloOk:
        """连接到 OpenClaw Gateway 并完成握手（challenge 模式 + 设备签名）"""
        async with self._connection_lock:
            if self.connected and self._hello is not None:
                return self._hello
            await self._disconnect()
            try:
                return await self._connect()
            except BaseException:
                # Cancellation also leaves partially opened sockets behind.
                await self._disconnect()
                raise

    async def _connect(self) -> HelloOk:
        log.info(f"Connecting to OpenClaw Gateway: {self._config.gateway_url}")

        try:
            ssl_context = None
            if self._config.gateway_url.startswith("wss://"):
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE

            connect_kwargs: Dict[str, Any] = {
                "max_size": 8 * 1024 * 1024,
                "ssl": ssl_context,
                "ping_interval": 60,
                "ping_timeout": 120,
            }
            if self._upstream_headers:
                # 在连接握手阶段附加自定义 header
                connect_kwargs["additional_headers"] = self._upstream_headers
            self._ws = await asyncio.wait_for(
                websockets.connect(self._config.gateway_url, **connect_kwargs),
                timeout=self._config.connection_timeout,
            )
        except asyncio.TimeoutError:
            raise ConnectionError(f"Connection timeout: {self._config.gateway_url}")
        except Exception as e:
            raise ConnectionError(f"Failed to connect: {e}")

        # OpenClaw 握手：接收 connect.challenge 事件，获取 nonce
        nonce = None
        try:
            raw = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._config.connection_timeout,
            )
            challenge_data = json.loads(raw)
            if challenge_data.get("type") == "event" and challenge_data.get("event") == "connect.challenge":
                payload = challenge_data.get("payload", {})
                nonce = payload.get("nonce", "")
                log.debug(f"Received connect.challenge, nonce={nonce[:8]}...")
            else:
                log.debug(f"Unexpected first frame (not challenge): {challenge_data.get('type')}/{challenge_data.get('event')}, proceeding anyway")
        except asyncio.TimeoutError:
            await self._ws.close()
            self._ws = None
            raise ConnectionError("Challenge timeout")

        # 加载或创建设备密钥对
        if self._device_private_key is None:
            try:
                self._device_private_key, self._device_public_key_b64, self._device_id = \
                    _load_or_create_device_keypair(self._device_key_file)
            except ImportError as e:
                log.warning(f"[device] 无法加载设备密钥，将使用无设备身份模式连接: {e}")

        # 发送 connect 请求（带设备签名）
        auth = None
        if self._config.gateway_token:
            auth = ConnectAuth(token=self._config.gateway_token)

        scopes = ["operator.read", "operator.write", "operator.admin"]

        # 构建设备签名（如果有设备密钥）
        device_identity: Optional[DeviceIdentity] = None
        if self._device_private_key and self._device_public_key_b64 and self._device_id and nonce:
            signed_at = int(time.time() * 1000)
            payload = _build_device_auth_payload_v3(
                device_id=self._device_id,
                client_id="gateway-client",
                client_mode="backend",
                role="operator",
                scopes=scopes,
                signed_at_ms=signed_at,
                token=self._config.gateway_token,
                nonce=nonce,
                platform="python",
                device_family=None,
            )
            signature = _sign_payload(self._device_private_key, payload)
            device_identity = DeviceIdentity(
                id=self._device_id,
                public_key=self._device_public_key_b64,
                signature=signature,
                signed_at=signed_at,
                nonce=nonce,
            )
            log.info(f"[connect] 已生成设备签名: device_id={self._device_id}")

        # The wire envelope is v3; newer OpenClaw gateways can negotiate v4
        # extensions. Advertise the supported range so both local 2026.3.x and
        # 2026.5+ gateways connect without environment-specific version pins.
        connect_params = ConnectParams(
            min_protocol=PROTOCOL_VERSION,
            max_protocol=OPENCLAW_GATEWAY_PROTOCOL_VERSION,
            client=ClientInfo(
                id="gateway-client",
                version="1.0.0",
                platform="python",
                mode="backend",
            ),
            # tool-events cap 让 gateway 推送 stream:"tool" 事件
            caps=["tool-events"],
            role="operator",
            # operator.read: 读权限, operator.write: 写权限, operator.admin: approvals 权限
            scopes=scopes,
            auth=auth,
            device=device_identity,
        )

        request_id = self._next_id()
        request = RequestFrame(
            id=request_id,
            method="connect",
            params=connect_params.to_dict(),
        )

        log.info(f"[connect] 发送连接请求: id={request_id}, scopes={scopes}, has_token={auth is not None}, has_device={device_identity is not None}")
        log.debug(f"[connect] 请求参数: {connect_params.to_dict()}")

        await self._ws.send(request.to_json())

        # 等待响应
        try:
            raw = await asyncio.wait_for(
                self._ws.recv(),
                timeout=self._config.connection_timeout,
            )
        except asyncio.TimeoutError:
            await self._ws.close()
            self._ws = None
            raise ConnectionError("Handshake timeout")

        response = ResponseFrame.from_dict(json.loads(raw))
        log.info(f"[connect] 收到响应: ok={response.ok}, error={response.error}")

        if not response.ok:
            await self._ws.close()
            self._ws = None
            error_msg = response.error.message if response.error else "Unknown error"
            error_details = response.error.details if response.error else None
            # 如果是设备身份相关错误，给出提示
            if error_details and isinstance(error_details, dict):
                detail_code = error_details.get("code", "")
                if detail_code.startswith("DEVICE_AUTH_"):
                    log.error(f"[connect] 设备认证失败: code={detail_code}, reason={error_details.get('reason')}")
            raise ConnectionError(f"Handshake failed: {error_msg}")

        self._hello = HelloOk.from_dict(response.payload)
        self._connected = True

        # 打印服务端返回的详细信息
        log.info(f"Connected to OpenClaw Gateway: protocol={self._hello.protocol}, conn_id={self._hello.server.conn_id}")
        log.info(f"[connect] 服务端响应 payload: {response.payload}")
        if self._hello.auth:
            log.info(f"[connect] 服务端授权信息: role={self._hello.auth.role}, scopes={self._hello.auth.scopes}")

        # 启动消息接收循环
        self._recv_task = asyncio.create_task(self._recv_loop())

        return self._hello

    async def disconnect(self) -> None:
        """断开连接"""
        async with self._connection_lock:
            await self._disconnect()

    async def _disconnect(self) -> None:
        self._closing = True
        self._connected = False
        self._hello = None
        recv_task, self._recv_task = self._recv_task, None
        ws, self._ws = self._ws, None
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(ConnectionError("Connection closed"))
        self._pending_requests.clear()
        try:
            if recv_task and recv_task is not asyncio.current_task():
                recv_task.cancel()
                try:
                    await recv_task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except Exception:
                    log.warning("OpenClaw receive task failed during cleanup", exc_info=True)
        finally:
            try:
                if ws is not None:
                    await ws.close()
            except Exception:
                log.warning("Failed to close OpenClaw socket", exc_info=True)
            finally:
                self._closing = False

    # ── 消息接收 ────────────────────────────────────────────────────────────

    async def _recv_loop(self) -> None:
        """消息接收循环"""
        ws = self._ws
        if ws is None:
            return

        try:
            async for raw in ws:
                if self._closing:
                    break

                try:
                    data = json.loads(raw)
                    frame_type = data.get("type")

                    if frame_type == "res":
                        response = ResponseFrame.from_dict(data)
                        future = self._pending_requests.pop(response.id, None)
                        if future and not future.done():
                            future.set_result(response)

                    elif frame_type == "event":
                        event = EventFrame.from_dict(data)
                        await self._dispatch_event(event)

                except json.JSONDecodeError as e:
                    log.warning(f"Invalid JSON frame: {e}")
                except Exception as e:
                    log.exception(f"Error processing frame: {e}")

        except websockets.ConnectionClosed:
            log.info("WebSocket connection closed")
        except Exception as e:
            log.exception(f"Recv loop error: {e}")
        finally:
            if self._ws is ws:
                self._connected = False
