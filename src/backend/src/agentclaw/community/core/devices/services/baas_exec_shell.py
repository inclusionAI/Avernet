"""BaaS shell command execution helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agentclaw.community.core.devices.errors import DeviceExecShellError
from agentclaw.community.kernel.device_dto import CommandResult
from agentclaw.community.log import get_logger


if TYPE_CHECKING:
    from agentclaw.community.core.devices.models import AllocatedDevice
    from agentclaw.community.core.service_bot.services.baas_service import BaasService


logger = get_logger()


def execute_baas_shell_command(
    *,
    baas_service: BaasService,
    device: AllocatedDevice,
    shell_cmd: str,
    timeout_seconds: int = 30,
) -> CommandResult:
    """在 BaaS Bot 容器内执行命令，并保持 Arca CommandResult 返回形态。"""
    bot_uuid = device.device_props.get("bot_uuid") or device.device_id
    try:
        logger.info(
            "[execute_baas_shell_command] bot_uuid=%s cmd=%s",
            bot_uuid,
            shell_cmd,
        )
        result = baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=shell_cmd,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        logger.error(
            "[execute_baas_shell_command] BaaS exec_shell failed: %s",
            e,
        )
        raise DeviceExecShellError(f"BaaS exec_shell fail: {e}") from e

    exit_code = int(result.get("exit_code", 0) or 0)
    execution_time_ms = float(result.get("execution_time_ms", 0) or 0)
    return CommandResult(
        stdout=str(result.get("stdout") or ""),
        stderr=str(result.get("stderr") or ""),
        exit_code=exit_code,
        elapsed_time=execution_time_ms / 1000,
        status="completed" if exit_code == 0 else "error",
        error=result.get("error"),
        extra={
            k: str(v)
            for k, v in result.items()
            if k
            not in {
                "stdout",
                "stderr",
                "exit_code",
                "execution_time_ms",
                "error",
            }
        },
    )
