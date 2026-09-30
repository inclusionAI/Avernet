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
        """Execute the 6-step init sequence inside the BaaS container."""
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

    def _run_baas_bootstrap(self, bot_uuid: str) -> None:
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd="bash /home/admin/bin/bootstrap_minimal.sh",
            timeout_seconds=60,
        )
        logger.info("[_run_baas_bootstrap] done: bot_uuid=%s", bot_uuid)

    def _run_baas_install_engine(self, bot_uuid: str) -> None:
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd="nohup bash /home/admin/bin/install_engine.sh >> /home/admin/logs/install_engine.log 2>&1 &",
            timeout_seconds=30,
        )
        logger.info("[_run_baas_install_engine] dispatched: bot_uuid=%s", bot_uuid)

    def _run_baas_setup_sync_service(self, bot_uuid: str, engine: str) -> None:
        cmd = (
            f"nohup bash /home/admin/bin/setup_supervisor_sync_service.sh {engine} "
            f">> /home/admin/logs/setup_supervisor_sync_service.log 2>&1 &"
        )
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=30,
        )
        logger.info("[_run_baas_setup_sync_service] dispatched: bot_uuid=%s", bot_uuid)

    def _ensure_baas_engine_dirs(self, bot_uuid: str, engine: str) -> None:
        cmd = (
            "mkdir -p $(dirname /home/admin/logs/engine_dirs_setup.log) && "
            f"nohup bash /home/admin/bin/setup_engine_dirs.sh {engine} "
            f">> /home/admin/logs/engine_dirs_setup.log 2>&1 &"
        )
        try:
            self._baas_service.exec_command_on_bot(
                bot_uuid=bot_uuid,
                cmd=cmd,
                timeout_seconds=30,
            )
            logger.info("[_ensure_baas_engine_dirs] dispatched: bot_uuid=%s", bot_uuid)
        except Exception as e:
            logger.warning("[_ensure_baas_engine_dirs] failed (non-fatal): %s", e)

    def _create_baas_skill_symlink_conf(self, bot_uuid: str, symbol: str | None) -> None:
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
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=start_cmd,
            timeout_seconds=30,
        )
        logger.info("[_start_baas_sandbox_service] dispatched: bot_uuid=%s", bot_uuid)

        watchdog_cmd = (
            f"nohup /home/admin/bin/starting_watchdog.sh --token {token} --client_id {client_id} "
            ">> /home/admin/logs/starting_watchdog.log 2>&1 &"
        )
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=watchdog_cmd,
            timeout_seconds=30,
        )
        logger.info("[_start_baas_sandbox_service] watchdog dispatched: bot_uuid=%s", bot_uuid)


def _deserialize_symbol(symbol: str | None) -> list[SynlinkMappingInfo]:
    if not symbol or not isinstance(symbol, str):
        return []
    try:
        return [SynlinkMappingInfo(**item) for item in json.loads(symbol)]
    except Exception as e:
        logger.error("[_deserialize_symbol] Failed: %s", e)
        return []
