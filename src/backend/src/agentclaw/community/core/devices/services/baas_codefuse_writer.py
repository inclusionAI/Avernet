"""BaaS 容器 codefuse.json 写入辅助。

从 ``baas_device_service.py`` 拆出，使该模块不超 1000 行上限(R9)。
逻辑与 ``arca_device_service._write_codefuse_token`` 对称，分别用
``exec_command_on_bot`` / ``_exec_cmd_in_sandbox`` 执行写入。
"""
from __future__ import annotations

from agentclaw.community.core.devices.services.baas_device_service import BaasDeviceServiceError
from agentclaw.community.log import get_logger

logger = get_logger()


def write_codefuse_token_baas(
    baas_service: object,
    bot_uuid: str,
    codefuse_token: str | None,
) -> None:
    """解码 auth_code 并在 BaaS 容器内写 codefuse.json（仅当 codefuse_token 非空）。

    在 start_service.sh 之前同步 exec 写入；容器 setup_cfuse.sh "非空保留" 会保住该
    token。解码失败 / 非零退出抛异常，由 _start_service 的 try/except 承接为
    (False, msg)，bot 停 FAILED（token 缺失则首聊鉴权失败，强一致阻断优于"假活"）。
    """
    if not codefuse_token:
        logger.info(
            "[_write_codefuse_token] codefuse_token empty, skip write: bot_uuid=%s",
            bot_uuid,
        )
        return
    from agentclaw.community.core.bot_management.codefuse_token import (
        build_codefuse_write_cmd_from_auth_code,
    )

    cmd = build_codefuse_write_cmd_from_auth_code(codefuse_token)
    result = baas_service.exec_command_on_bot(
        bot_uuid=bot_uuid, cmd=cmd, timeout_seconds=30
    )
    exit_code = result.get("exit_code", -1) if isinstance(result, dict) else -1
    if exit_code != 0:
        raise BaasDeviceServiceError(
            f"write codefuse.json failed: bot_uuid={bot_uuid} "
            f"exit_code={exit_code} result={result}"
        )
    logger.info("[_write_codefuse_token] codefuse.json written: bot_uuid=%s", bot_uuid)
