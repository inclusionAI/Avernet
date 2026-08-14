"""BaaS container initialization helpers."""

from __future__ import annotations

import json

from agentclaw.community.core.devices.models import AllocatedDevice, SynlinkMappingInfo
from agentclaw.community.log import get_logger

logger = get_logger()


class BaasContainerInitializer:
    """Run the post-publish bootstrap sequence inside a BaaS container."""

    def __init__(self, baas_service) -> None:
        self._baas_service = baas_service

    def run(
        self,
        *,
        bot_uuid: str,
        device: AllocatedDevice,
        engine: str,
        bot_type: str,
        bot_id: str | None,
        owner_id: str | None,
        callback_token: str,
        admins: list[str] | None,
        codefuse_token: str | None = None,
    ) -> None:
        """Execute the ordered init sequence inside the BaaS container."""
        entity_id = device.device_props.get("entity_id")
        entity_type = device.device_props.get("entity_type")
        client_id = device.device_props.get("client_id", "")
        symbol = device.device_props.get("symbol")

        self._run_baas_bootstrap(bot_uuid)
        self._run_baas_install_engine(bot_uuid)
        self._run_baas_setup_sync_service(bot_uuid, engine)
        self._ensure_baas_engine_dirs(bot_uuid, engine)
        self._create_baas_skill_symlink_conf(bot_uuid, symbol)
        self._write_codefuse_token(bot_uuid, codefuse_token)
        self._wait_for_baas_supervisor(bot_uuid)
        self._start_baas_sandbox_service(
            bot_uuid=bot_uuid,
            client_id=client_id,
            engine=engine,
            bot_type=bot_type,
            bot_id=bot_id,
            owner_id=owner_id,
            token=callback_token,
            entity_id=entity_id,
            entity_type=entity_type,
            stage="draft",
            admins=",".join(admins) if admins else None,
        )

    def _write_codefuse_token(self, bot_uuid: str, codefuse_token: str | None) -> None:
        """解码 auth_code 并在容器内写 codefuse.json（委托 baas_codefuse_writer）。"""
        from agentclaw.community.core.devices.services.baas_codefuse_writer import (
            write_codefuse_token_baas,
        )

        write_codefuse_token_baas(self._baas_service, bot_uuid, codefuse_token)

    def _exec_checked(
        self,
        *,
        bot_uuid: str,
        cmd: str,
        timeout_seconds: int,
        step: str,
    ) -> dict:
        """Execute one required initialization step and fail on non-zero exit."""
        result = self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=timeout_seconds,
        )
        exit_code = result.get("exit_code", -1) if isinstance(result, dict) else -1
        if exit_code != 0:
            stderr = result.get("stderr", "") if isinstance(result, dict) else ""
            stderr_tail = str(stderr or "")[-1000:]
            raise RuntimeError(
                f"BaaS container init step failed: step={step} "
                f"bot_uuid={bot_uuid} exit_code={exit_code} stderr={stderr_tail}"
            )
        logger.info(
            "[BaasContainerInitializer] step completed: step=%s bot_uuid=%s",
            step,
            bot_uuid,
        )
        return result

    def _run_baas_bootstrap(self, bot_uuid: str) -> None:
        # The image root_init process can bootstrap the same checkout. Wait for
        # image initialization before running the Backend compensation step so
        # install_engine never observes a checkout being replaced concurrently.
        cmd = (
            "_agentclaw_image_ready=0; "
            "for _agentclaw_wait in $(seq 1 120); do "
            "if [ -f /var/run/agentclaw/.install_dependency_file ] || "
            '[ "$(cat /proc/1/comm 2>/dev/null)" = "supervisord" ]; then '
            "_agentclaw_image_ready=1; break; "
            "fi; "
            "sleep 1; "
            "done; "
            'if [ "$_agentclaw_image_ready" != "1" ]; then '
            "echo '[bootstrap] container image initialization timed out' >&2; "
            "exit 1; "
            "fi; "
            "bash /home/admin/bin/bootstrap_minimal.sh"
        )
        self._exec_checked(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=300,
            step="bootstrap",
        )

    def _run_baas_install_engine(self, bot_uuid: str) -> None:
        cmd = (
            "mkdir -p /home/admin/logs && "
            "if [ -f /home/admin/bin/install_engine.sh ]; then "
            "bash /home/admin/bin/install_engine.sh "
            ">> /home/admin/logs/install_engine.log 2>&1; "
            "else echo '[install_engine] script not found, skip'; fi"
        )
        self._exec_checked(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=300,
            step="install_engine",
        )

    def _run_baas_setup_sync_service(self, bot_uuid: str, engine: str) -> None:
        cmd = (
            "mkdir -p /home/admin/logs && "
            f"bash /home/admin/bin/setup_supervisor_sync_service.sh {engine} "
            f">> /home/admin/logs/setup_supervisor_sync_service.log 2>&1"
        )
        self._exec_checked(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=120,
            step="setup_supervisor_sync_service",
        )

    def _ensure_baas_engine_dirs(self, bot_uuid: str, engine: str) -> None:
        cmd = (
            "mkdir -p /home/admin/logs && "
            f"bash /home/admin/bin/setup_engine_dirs.sh {engine} "
            f">> /home/admin/logs/engine_dirs_setup.log 2>&1"
        )
        try:
            self._exec_checked(
                bot_uuid=bot_uuid,
                cmd=cmd,
                timeout_seconds=120,
                step="setup_engine_dirs",
            )
        except Exception as exc:
            logger.warning(
                "[_ensure_baas_engine_dirs] failed (non-fatal): bot_uuid=%s error=%s",
                bot_uuid,
                exc,
            )

    def _wait_for_baas_supervisor(self, bot_uuid: str) -> None:
        cmd = (
            "for _agentclaw_wait in $(seq 1 60); do "
            "if supervisorctl pid >/dev/null 2>&1; then exit 0; fi; "
            "sleep 1; "
            "done; "
            "echo '[supervisor] readiness timed out' >&2; "
            "exit 1"
        )
        self._exec_checked(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=70,
            step="wait_supervisor_ready",
        )

    def _create_baas_skill_symlink_conf(
        self, bot_uuid: str, symbol: str | None
    ) -> None:
        mappings = _deserialize_symbol(symbol)
        if not mappings:
            return

        config_lines = [f"{m.source.lstrip('./')} {m.target}" for m in mappings]
        config_content = "\n".join(config_lines)

        cmd = (
            "mkdir -p /var/run/agentclaw && "
            "cat > /var/run/agentclaw/skill-symlinks.conf << 'EOFCONF'\n"
            f"{config_content}\nEOFCONF"
        )
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=30,
        )
        logger.info("[_create_baas_skill_symlink_conf] written: bot_uuid=%s", bot_uuid)

    def _start_baas_sandbox_service(
        self,
        *,
        bot_uuid: str,
        client_id: str,
        engine: str,
        bot_type: str = "",
        bot_id: str | None = None,
        owner_id: str | None = None,
        token: str = "",
        entity_id: str | None = None,
        entity_type: str | None = None,
        stage: str | None = None,
        admins: str | None = None,
    ) -> None:
        cmd = f"/home/admin/bin/start_service.sh --token {token} --client_id {client_id} --engine {engine}"
        if bot_type:
            cmd += f" --bot_type {bot_type}"
        if bot_id:
            cmd += f" --bot_id {bot_id}"
        if owner_id:
            cmd += f" --owner_id {owner_id}"
        if entity_id and entity_type:
            cmd += f" --entity_id {entity_id} --entity_type {entity_type}"
        if stage:
            cmd += f" --stage {stage}"
        if admins:
            cmd += f" --admins {admins}"

        start_cmd = f"nohup {cmd} >> /home/admin/start.log 2>&1 &"
        self._exec_checked(
            bot_uuid=bot_uuid,
            cmd=start_cmd,
            timeout_seconds=30,
            step="start_service",
        )

        watchdog_cmd = (
            f"nohup /home/admin/bin/starting_watchdog.sh --token {token} --client_id {client_id} "
            ">> /home/admin/logs/starting_watchdog.log 2>&1 &"
        )
        self._exec_checked(
            bot_uuid=bot_uuid,
            cmd=watchdog_cmd,
            timeout_seconds=30,
            step="starting_watchdog",
        )


def _deserialize_symbol(symbol: str | None) -> list[SynlinkMappingInfo]:
    if not symbol or not isinstance(symbol, str):
        return []
    try:
        return [SynlinkMappingInfo(**item) for item in json.loads(symbol)]
    except Exception as e:
        logger.error("[_deserialize_symbol] Failed: %s", e)
        return []
