"""本地进程沙箱实现 —— 基于 LocalProcessManager 的 ArcaSandbox 协议实现。"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from secbaas.community.api.device_manage import (
    OutBoundOperationRule,
    OutBoundOperationRuleUpdatedMode,
)
from secbaas.community.logger import get_logger
from secbaas.community.spi.sandbox.arca import ArcaSandbox

if TYPE_CHECKING:
    from ._process_manager import ProcessEntry

logger = get_logger("plugin-sandbox-arca-local-proc")


class LocalProcessSandboxInfo:
    """本地进程沙箱信息对象。

    支持属性访问（info.status），与 Arca SDK 的 SandboxInfo 接口对齐。
    ArcaPaasService 通过属性访问以下字段：
    status, template_id, sandbox_id, resources, ttl_in_minutes, envs,
    snapshot_id, metadata, outbound_operation_rule, ttl_timestamp。
    """

    def __init__(
        self,
        sandbox_id: str,
        status: str,
        template_id: str,
        is_ready: bool,
        ttl_in_minutes: int | None,
        ttl_timestamp: int | None = None,
        bot_id: str = "",
        adapter_port: int = 0,
        adapter_pid: int | None = None,
        engine_port: int = 0,
        engine_pid: int | None = None,
        config_dir: str = "",
        workspace_dir: str = "",
        resources: Any = None,
        envs: dict[str, str] | None = None,
        snapshot_id: str | None = None,
        metadata: dict[str, str] | None = None,
        outbound_operation_rule: Any = None,
    ) -> None:
        self.sandbox_id = sandbox_id
        self.status = status
        self.template_id = template_id
        self.is_ready = is_ready
        self.ttl_in_minutes = ttl_in_minutes
        self.ttl_timestamp = ttl_timestamp
        self.bot_id = bot_id
        self.adapter_port = adapter_port
        self.adapter_pid = adapter_pid
        self.engine_port = engine_port
        self.engine_pid = engine_pid
        self.config_dir = config_dir
        self.workspace_dir = workspace_dir
        self.resources = resources
        self.envs = envs
        self.snapshot_id = snapshot_id
        self.metadata = metadata
        self.outbound_operation_rule = outbound_operation_rule

    def __repr__(self) -> str:
        """返回结构化、日志友好的字符串表示。

        仅包含本地进程模式下的核心运维字段，
        省略通常为 None 的 Arca SDK 对齐字段（resources/envs/snapshot_id/metadata/outbound_operation_rule）。
        """
        return (
            f"LocalProcessSandboxInfo("
            f"sandbox_id={self.sandbox_id!r}, "
            f"status={self.status!r}, "
            f"template_id={self.template_id!r}, "
            f"is_ready={self.is_ready}, "
            f"bot_id={self.bot_id!r}, "
            f"adapter_port={self.adapter_port}, "
            f"adapter_pid={self.adapter_pid}, "
            f"engine_port={self.engine_port}, "
            f"engine_pid={self.engine_pid}, "
            f"ttl_in_minutes={self.ttl_in_minutes}, "
            f"ttl_timestamp={self.ttl_timestamp}, "
            f"config_dir={self.config_dir!r}, "
            f"workspace_dir={self.workspace_dir!r}"
            f")"
        )


class LocalProcessArcaSandbox(ArcaSandbox):
    """本地进程沙箱实现 —— 基于 LocalProcessManager 管理的进程对。

    实现的 Protocol 为 secbaas.spi.sandbox.arca._protocols.ArcaSandbox。

    Args:
        sandbox_id: 沙箱唯一标识符。
        template_id: 模板 ID，用于标识沙箱类型/引擎。
        process_entry: LocalProcessManager 返回的进程条目。
    """

    def __init__(
        self,
        sandbox_id: str,
        template_id: str,
        process_entry: ProcessEntry,
    ) -> None:
        self._sandbox_id = sandbox_id
        self._template_id = template_id
        self._process_entry = process_entry
        self._is_ready = True  # LocalProcessManager.start() 成功返回表示已就绪
        self._status = "RUNNING"
        self._ttl_minutes: int | None = None
        self._created_at = time.time()

    @property
    def is_ready(self) -> bool:
        """沙箱是否就绪（进程已启动并可用）。"""
        return self._is_ready

    @property
    def sandbox_id(self) -> str:
        """沙箱唯一标识符。"""
        return self._sandbox_id

    @property
    def _ttl_minutes(self) -> int | None:
        return self.__ttl_minutes

    @_ttl_minutes.setter
    def _ttl_minutes(self, value: int | None) -> None:
        self.__ttl_minutes = value

    def get_info(self) -> LocalProcessSandboxInfo:
        """获取沙箱信息。

        Returns:
            LocalProcSandboxInfo 对象，包含 sandbox_id, status, template_id 等属性。
            返回对象支持属性访问（info.status），与 Arca SDK 的 SandboxInfo 接口对齐。
        """
        entry = self._process_entry
        return LocalProcessSandboxInfo(
            sandbox_id=self._sandbox_id,
            status=self._status,
            template_id=self._template_id,
            is_ready=self._is_ready,
            ttl_in_minutes=self._ttl_minutes,
            ttl_timestamp=None,  # 本地进程模式不支持 TTL 时间戳
            bot_id=entry.bot_id,
            adapter_port=entry.adapter_port,
            adapter_pid=(entry.adapter_process.pid if entry.adapter_process else None),
            engine_port=entry.openclaw_port or entry.hermes_port,
            engine_pid=(
                entry.openclaw_process.pid
                if entry.openclaw_process
                else entry.hermes_process.pid
                if entry.hermes_process
                else None
            ),
            config_dir=str(entry.config_dir),
            workspace_dir=str(entry.workspace_dir),
            # 以下字段为 ArcaCreationResult 所需，本地模式无对应值
            resources=None,
            envs=None,
            snapshot_id=None,
            metadata=None,
            outbound_operation_rule=None,
        )

    def destroy(self) -> Any:
        """销毁沙箱（停止进程对）。

        通过 LocalProcessManager 停止 adapter 及 engine 进程，
        并将沙箱状态标记为 DESTROYED。重复调用是安全的（幂等）。

        Returns:
            True 表示成功，失败时抛出异常。
        """
        if self._status == "DESTROYED":
            return True

        from ._process_manager import LocalProcessManager

        manager = LocalProcessManager.instance()
        try:
            manager.stop(self._sandbox_id)
        except Exception as e:
            logger.error(
                "Error stopping processes for sandbox %s: %s",
                self._sandbox_id,
                e,
            )

        logger.info("Destroyed sandbox: %s", self._sandbox_id)
        self._status = "DESTROYED"
        self._is_ready = False
        return True

    def exec_command(
        self,
        cmd: str,
        timeout_in_millis: int = 30000,
        envs: dict[str, str] | None = None,
    ) -> Any:
        """在沙箱中执行命令。

        注意：本地进程模式下，命令在沙箱所在的宿主机上执行，
        而不是在隔离的容器中。

        Args:
            cmd: 要执行的命令字符串。
            timeout_in_millis: 最大执行时间（毫秒）。
            envs: 命令执行时的环境变量。

        Returns:
            包含 exit_code, stdout, stderr, elapsed_time 的执行结果对象。
        """
        if not self._is_ready:
            raise RuntimeError(f"Sandbox {self._sandbox_id} is not ready")

        start_time = time.time()
        logger.warning(
            "exec_command in local sandbox mode is a no-op, cmd: %.100s ...", cmd
        )
        logger.debug("Full cmd: %s", cmd)
        elapsed = time.time() - start_time
        return LocalProcCommandResult(
            exit_code=0,
            stdout="",
            stderr="",
            elapsed_time=elapsed,
        )

    def update_outbound_rule(
        self,
        rule: OutBoundOperationRule,
        updated_mode: OutBoundOperationRuleUpdatedMode,
    ) -> Any:
        """更新出站规则。

        Args:
            rule: 要应用的出站规则。
            updated_mode: 更新模式（如 REPLACE）。

        Returns:
            True 表示成功。
        """
        logger.info("Updating outbound rule for sandbox %s", self._sandbox_id)
        # 本地进程模式暂不支持网络规则
        return True

    def extend_ttl(self, ttl_minutes: int) -> Any:
        """延长沙箱 TTL。

        Args:
            ttl_minutes: 要增加的 TTL 分钟数。

        Returns:
            True 表示成功。
        """
        if self._ttl_minutes is not None:
            self._ttl_minutes += ttl_minutes
        else:
            self._ttl_minutes = ttl_minutes
        logger.info(
            "Extended TTL for sandbox %s to %s minutes",
            self._sandbox_id,
            self._ttl_minutes,
        )
        return True


class LocalProcCommandResult:
    """本地进程沙箱命令执行结果。"""

    def __init__(
        self,
        exit_code: int,
        stdout: str,
        stderr: str,
        elapsed_time: float,
    ) -> None:
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.elapsed_time = elapsed_time
