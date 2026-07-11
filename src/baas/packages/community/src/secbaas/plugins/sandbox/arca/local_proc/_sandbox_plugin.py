"""本地进程沙箱插件实现 —— 基于 LocalProcessManager 的 ArcaSandbox 工厂实现。"""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from secbaas.api.bot_runtime import HttpConnectionInfo, WsConnectionInfo
from secbaas.api.device_manage import (
    MountPoint,
    OutBoundOperationRule,
    ResourceSpecification,
    Storage,
)
from secbaas.logger import get_logger
from secbaas.spi.sandbox.arca import ArcaSandbox, ArcaSandboxPlugin

from ._process_manager import LocalProcessManager
from ._sandbox import LocalProcessArcaSandbox
from ._workspace import resolve_workspace_dir, setup_workspace_dirs

logger = get_logger("plugin-sandbox-arca-local-proc")

# 引擎类型映射
ENGINE_MAP = {
    "aicoding": "aicoding",
    "aicoding-default": "aicoding",
    "openclaw": "openclaw",
    "openclaw-default": "openclaw",
    "hermes": "hermes",
    "hermes-default": "hermes",
    "claude_code": "claude_code",
}


def _resolve_engine(
    *,
    envs: dict[str, str] | None,
    metadata: dict[str, str] | None,
) -> str:
    """Resolve the per-sandbox engine before falling back to process defaults."""
    requested = (
        (envs or {}).get("AGENTCLAW_ENGINE")
        or (metadata or {}).get("engine")
        or os.environ.get("CHAT_ENGINE")
        or "openclaw"
    )
    return ENGINE_MAP.get(requested, requested)


def _open_callback_request(
    request: urllib.request.Request,
    *,
    timeout: float,
) -> Any:
    """Open local callbacks directly so macOS system proxies cannot intercept them."""
    hostname = urllib.parse.urlsplit(request.full_url).hostname
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)
    return urllib.request.urlopen(request, timeout=timeout)


class LocalProcessArcaSandboxPlugin(ArcaSandboxPlugin):
    """本地进程沙箱插件实现。

    基于 LocalProcessManager 管理本地 adapter + engine 进程对，
    模拟 Arca 沙箱的生命周期。

    实现的 Protocol 为 secbaas.spi.sandbox.arca._protocols.ArcaSandboxPlugin。

    Args:
        credentials: Arca 凭证（本地进程模式不使用，保留以对齐工厂签名）。
    """

    def __init__(
        self,
        credentials: Any | None = None,
    ) -> None:
        self._manager = LocalProcessManager.instance()
        self._sandboxes: dict[str, LocalProcessArcaSandbox] = {}

    def create_sync_sandbox(
        self,
        template_id: str,
        ttl_in_minutes: int | None = None,
        envs: dict[str, str] | None = None,
        mount_points: list[MountPoint] | None = None,
        resource_spec: ResourceSpecification | None = None,
        metadata: dict[str, str] | None = None,
        outbound_operation_rule: OutBoundOperationRule | None = None,
        storage: Storage | None = None,
        image: str | None = None,
        timeout_in_millis: int = 60000,
        ready_timeout_in_seconds: int = 60,
    ) -> ArcaSandbox:
        """创建新的沙箱并等待其就绪。

        Args:
            template_id: 平台模板 ID（如 aicoding-default, openclaw, hermes）。
            ttl_in_minutes: 生存时间（分钟）。
            envs: 要在沙箱中设置的环境变量。
            mount_points: 存储挂载点配置。
            resource_spec: CPU/内存资源规格。
            metadata: 任意传递的元数据（包含 bot_id, callback_token, entity_id 等）。
            outbound_operation_rule: 网络出站规则。
            storage: NAS 存储绑定配置。
            image: Docker 镜像覆盖（覆盖 template 默认镜像）。
            timeout_in_millis: 最大创建等待时间（毫秒）。
            ready_timeout_in_seconds: 等待沙箱就绪状态的最大时间。

        Returns:
            一个可供使用的 ArcaSandbox。

        Raises:
            RuntimeError: 创建失败或超时。
        """
        logger.info(
            "Creating sandbox with all params: "
            "template_id=%s, ttl_in_minutes=%s, envs=%s, mount_points=%s, "
            "resource_spec=%s, metadata=%s, outbound_operation_rule=%s, "
            "storage=%s, timeout_in_millis=%s, ready_timeout_in_seconds=%s",
            template_id,
            ttl_in_minutes,
            envs,
            mount_points,
            resource_spec,
            metadata,
            outbound_operation_rule,
            storage,
            timeout_in_millis,
            ready_timeout_in_seconds,
        )

        # 确定引擎类型
        engine = _resolve_engine(envs=envs, metadata=metadata)

        # 从 metadata 提取必要信息
        # Check required fields
        if not metadata:
            raise ValueError("metadata should not be None")
        if "tc_bot_id" not in metadata:
            raise ValueError("metadata should contain tc_bot_id")
        if "device_uuid" not in metadata:
            raise ValueError("metadata should contain device_uuid")
        if "entity_id" not in metadata:
            raise ValueError("metadata should contain entity_id")
        if "entity_type" not in metadata:
            raise ValueError("metadata should contain entity_type")

        tc_bot_id = metadata.get("tc_bot_id")

        inner_metadata = metadata.copy()
        # other metadata
        inner_metadata.setdefault("engine", engine)
        inner_metadata.setdefault("callback_token", "local_mock")
        inner_metadata.setdefault("agent_code", "local_mock")

        # 确定 workspace 目录
        workspace_dir = resolve_workspace_dir(inner_metadata, tc_bot_id)
        setup_workspace_dirs(workspace_dir, engine)

        # Extract required fields
        device_id = inner_metadata["device_uuid"]
        entity_id = inner_metadata.get("entity_id")
        callback_token = inner_metadata["callback_token"]
        agent_code = metadata.get("agent_code")

        # Optional fields
        admins = metadata.get("admins") if metadata else None
        symbol_json = metadata.get("symbol_json") if metadata else None

        logger.info(
            "Creating sandbox: device_id=%s, bot_id=%s, engine=%s, template_id=%s",
            device_id,
            tc_bot_id,
            engine,
            template_id,
        )

        # Step 1: 分配端口
        adapter_port, engine_port = self._manager.allocate_ports(engine=engine)
        logger.info("Allocated ports: adapter=%s, engine=%s", adapter_port, engine_port)

        # Step 2: 创建引擎配置目录
        config_dir: Path
        if engine == "openclaw":
            config_dir = self._manager.create_openclaw_config(
                bolt_id=tc_bot_id,
                openclaw_port=engine_port,
                workspace_dir=workspace_dir,
                entity_id=entity_id,
            )
        elif engine == "hermes":
            config_dir = self._manager.create_hermes_config(
                bolt_id=tc_bot_id,
                hermes_port=engine_port,
                workspace_dir=workspace_dir,
            )
        else:
            # aicoding 等引擎不需要配置目录
            config_dir = workspace_dir / ".config"
            config_dir.mkdir(parents=True, exist_ok=True)

        # Step 3: 启动进程
        try:
            entry = self._manager.start(
                device_id=device_id,
                bot_id=tc_bot_id,
                adapter_port=adapter_port,
                engine_port=engine_port,
                config_dir=config_dir,
                workspace_dir=workspace_dir,
                callback_token=callback_token,
                entity_id=entity_id,
                symbol_json=symbol_json,
                agent_code=agent_code,
                engine=engine,
                admins=admins,
            )
        except Exception as e:
            # 进程启动失败，发送 FAILED 回调
            self._send_device_callback(
                device_id,
                metadata,
                result_status="FAILED",
                exit_code=1,
                stderr=str(e),
            )
            raise

        # Step 4: 创建沙箱对象
        sandbox = LocalProcessArcaSandbox(
            sandbox_id=entry.sandbox_id,
            template_id=template_id,
            process_entry=entry,
        )
        sandbox._ttl_minutes = ttl_in_minutes
        logger.info("%s", sandbox.get_info())

        # 注册沙箱
        self._sandboxes[entry.sandbox_id] = sandbox
        logger.info(
            "Sandbox created: device_id=%s, adapter_port=%s, engine_port=%s",
            device_id,
            adapter_port,
            engine_port,
        )

        # Step 5: 异步发送 device-callback 通知发布服务沙箱已创建（延迟 3 秒，先返回沙箱对象）
        import threading

        def _delayed_callback() -> None:
            self._send_device_callback(
                device_id,
                metadata,
                result_status="SUCCESS",
                exit_code=0,
                stdout="sandbox created",
            )

        timer = threading.Timer(3, _delayed_callback)
        timer.daemon = True
        timer.start()
        logger.info("Scheduled device-callback in 3s for device_id=%s", device_id)

        return sandbox

    def _send_device_callback(
        self,
        device_id: str,
        metadata: dict[str, str] | None,
        *,
        result_status: str,
        exit_code: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        """发送 device-callback 到发布服务，通知沙箱创建结果。

        使用 LOCAL_CALLBACK_URL 环境变量作为回调服务器基础 URL。
        如果环境变量或关键 metadata 缺失，则跳过回调并记录警告日志。

        Args:
            device_id: 设备 ID（即 device_uuid）。
            metadata: 创建沙箱时传入的元数据，需包含 publish_id 和 tenant。
            result_status: 回调结果状态，"SUCCESS" 或 "FAILED"。
            exit_code: 进程退出码，0 表示成功。
            stdout: 标准输出内容。
            stderr: 标准错误内容。
        """
        import json
        import urllib.error

        callback_url = os.environ.get(
            "LOCAL_CALLBACK_URL", "http://localhost:8890"
        ).rstrip("/")
        if not callback_url:
            logger.warning(
                "LOCAL_CALLBACK_URL not set, skipping device-callback for %s",
                device_id,
            )
            return

        publish_id = metadata.get("publish_id") if metadata else None
        tenant = metadata.get("tenant") if metadata else None

        if not publish_id:
            logger.warning(
                "publish_id not in metadata, skipping device-callback for %s",
                device_id,
            )
            return

        payload = {
            "device_uuid": device_id,
            "publish_id": int(publish_id),
            "event_type": "start",
            "result_status": result_status,
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "tenant": tenant or "",
        }

        url = f"{callback_url}/api/v1/publish/device-callback"
        logger.info(
            "Sending device callback: url=%s, device=%s, publish_id=%s, "
            "status=%s, tenant=%s, payload=%s",
            url,
            device_id,
            publish_id,
            result_status,
            tenant,
            json.dumps(payload),
        )
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _open_callback_request(req, timeout=5) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                logger.info(
                    "Device callback response: url=%s, status=%s, body=%s",
                    url,
                    resp.status,
                    body,
                )
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            logger.error(
                "Device callback failed: url=%s, http_status=%s, body=%s",
                url,
                e.code,
                body,
            )
        except Exception:
            logger.exception(
                "Failed to send device callback for %s to %s",
                device_id,
                url,
            )

    def connect_sync_sandbox(self, sandbox_id: str) -> ArcaSandbox:
        """连接到已存在的沙箱。

        Args:
            sandbox_id: 要连接的沙箱 ID。

        Returns:
            已存在沙箱的 ArcaSandbox。

        Raises:
            RuntimeError: 沙箱未找到或无法连接。
        """
        # 先检查本地缓存
        if sandbox_id in self._sandboxes:
            sandbox = self._sandboxes[sandbox_id]
            if sandbox._status == "DESTROYED":
                raise RuntimeError(f"Sandbox {sandbox_id} has been destroyed")
            return sandbox

        # 从 manager 获取 entry
        entry = self._manager.get_entry(sandbox_id)
        if entry is None:
            raise RuntimeError(f"Sandbox {sandbox_id} not found")

        # 创建沙箱对象
        # 根据 engine 类型确定 template_id
        if entry.hermes_port > 0:
            template_id = "hermes"
        elif entry.openclaw_port > 0:
            template_id = "openclaw"
        else:
            template_id = "aicoding"

        sandbox = LocalProcessArcaSandbox(
            sandbox_id=sandbox_id,
            template_id=template_id,
            process_entry=entry,
        )

        # 缓存
        self._sandboxes[sandbox_id] = sandbox
        logger.info("Connected to sandbox: %s", sandbox_id)
        return sandbox

    def list_sandboxes(self) -> list[dict[str, Any]]:
        """列出所有管理的沙箱。

        Returns:
            沙箱信息列表。
        """
        result = []
        for sandbox_id, sandbox in self._sandboxes.items():
            result.append(
                {
                    "sandbox_id": sandbox_id,
                    "status": sandbox._status,
                    "is_ready": sandbox.is_ready,
                    "template_id": sandbox._template_id,
                }
            )
        return result

    def close(self) -> None:
        """释放插件持有的任何资源。"""
        for sandbox_id, sandbox in list(self._sandboxes.items()):
            try:
                sandbox.destroy()
                logger.info("Destroyed sandbox: %s", sandbox_id)
            except Exception as e:
                logger.error("Error destroying sandbox %s: %s", sandbox_id, e)

        self._sandboxes.clear()
        logger.info("LocalProcArcaSandboxPlugin closed")

    def resolve_ws_conn_info(
        self,
        paas_device_id: str,
        port: int,
        path: str,
        template_id: int | None = None,
    ) -> WsConnectionInfo:
        """解析沙箱设备的 WebSocket 连接信息。

        Args:
            paas_device_id: Arca sandbox_id（原始平台设备 ID）。
            port: 设备上的目标端口。
            path: WebSocket 路径（例如 /api/openclaw/ws）。
            template_id: 可选的模板 ID，用于多租户 proxypass 路由。

        Returns:
            包含 wss:// URL、JWT token、target 和 expiry 的 WsConnectionInfo。
        """

        entry = self._manager.get_entry(paas_device_id)
        if entry is None:
            raise RuntimeError(f"Device {paas_device_id} not found")

        # 使用 adapter 端口
        adapter_port = entry.adapter_port
        # 规范化 path：补前导斜杠，避免拼成 "localhost:20018api/..." 缺斜杠
        norm_path = path if path.startswith("/") else f"/{path}"

        return WsConnectionInfo(
            ws_url=f"ws://localhost:{adapter_port}{norm_path}",
            token="local",
            target=f"localhost:{adapter_port}",
            expires_at=datetime.now() + timedelta(days=1),
        )

    def resolve_http_connection_info(
        self,
        paas_device_id: str,
        port: int,
        path: str = "/",
        template_id: int | None = None,
    ) -> HttpConnectionInfo:
        """解析沙箱设备的 HTTP 连接信息。

        Args:
            paas_device_id: Arca sandbox_id（原始平台设备 ID）。
            port: 目标端口。
            path: HTTP 路径（默认为 "/"）。
            template_id: 可选的模板 ID，用于多租户 proxypass 路由。

        Returns:
            包含 http_url、token 和 expires_at 的 HttpConnectionInfo。
        """
        entry = self._manager.get_entry(paas_device_id)
        if entry is None:
            raise RuntimeError(f"Device {paas_device_id} not found")

        # 使用 adapter 端口（http 走 adapter 而非入参 port）+ path 补前导斜杠
        adapter_port = entry.adapter_port
        norm_path = path if path.startswith("/") else f"/{path}"

        return HttpConnectionInfo(
            http_url=f"http://localhost:{adapter_port}{norm_path}",
            token="local",
        )
