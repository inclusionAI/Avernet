"""BaaS container initialization helpers."""

from __future__ import annotations

import json
import shlex

from agentclaw.community.core.devices.models import AllocatedDevice, SynlinkMappingInfo
from agentclaw.community.log import get_logger

logger = get_logger()


class BaasContainerInitializer:
    """Complete Backend configuration and confirm the existing BaaS startup."""

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
        startup_identity: str,
        admins: list[str] | None,
        codefuse_token: str | None = None,
    ) -> None:
        """Finish post-publish handoff without repeating one-shot service startup.

        BaaS has already completed start_service.sh, including security cleanup.
        Keep the existing configuration steps and report its preserved result
        with the Backend Binding identity. Business startup arguments remain
        accepted for caller compatibility; this handoff does not rewrite the
        runtime's credentials or restart its processes.
        """
        client_id = str(device.device_props.get("client_id") or device.device_id)
        symbol = device.device_props.get("symbol")
        envs = device.device_props.get("envs")
        effective_startup_identity = (
            startup_identity
            if isinstance(envs, dict)
            and envs.get("AGENTCLAW_SKILLS_LAYOUT") == "pool"
            else None
        )

        self._run_baas_bootstrap(bot_uuid)
        self._run_baas_install_engine(bot_uuid)
        self._run_baas_setup_sync_service(bot_uuid, engine)
        self._ensure_baas_engine_dirs(bot_uuid, engine)
        if self._create_baas_skill_symlink_conf(bot_uuid, symbol):
            self._apply_baas_skill_symlinks(bot_uuid, engine)
        self._write_codefuse_token(bot_uuid, codefuse_token)
        self.dispatch_watchdog(
            bot_uuid=bot_uuid,
            client_id=client_id,
            token=callback_token,
            startup_identity=effective_startup_identity,
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

    def _create_baas_skill_symlink_conf(self, bot_uuid: str, symbol: str | None) -> bool:
        mappings = _deserialize_symbol(symbol)
        if not mappings:
            return False

        config_lines = [f"{m.source.lstrip('./')} {m.target}" for m in mappings]
        config_content = "\n".join(config_lines)

        cmd = (
            "mkdir -p /var/run/agentclaw && "
            "cat > /var/run/agentclaw/skill-symlinks.conf << 'EOFCONF'\n"
            f"{config_content}\nEOFCONF"
        )
        result = self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=cmd,
            timeout_seconds=30,
        )
        if result.get("exit_code") != 0:
            raise RuntimeError("BaaS skill symlink config write failed")
        logger.info("[_create_baas_skill_symlink_conf] written: bot_uuid=%s", bot_uuid)
        return True

    def _apply_baas_skill_symlinks(self, bot_uuid: str, engine: str) -> None:
        """Consume nonempty legacy symbol config without running full startup."""
        if engine in {"aicoding", "claude_code"}:
            command = "/home/admin/bin/aicoding/setup_skill_symlinks.sh"
        else:
            command = shlex.join(["/home/admin/bin/setup_skill_symlinks.sh", "--engine", engine])
        result = self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid, cmd=_admin_command(command), timeout_seconds=30,
        )
        if result.get("exit_code") != 0:
            raise RuntimeError("BaaS skill symlink application failed")

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
        startup_identity: str | None,
    ) -> None:
        """Legacy explicit-start compatibility helper; not used by run()."""
        args = [
            "/home/admin/bin/start_service.sh",
            "--token",
            token,
            "--client_id",
            client_id,
            "--engine",
            engine,
        ]
        if startup_identity:
            args.extend(("--startup_identity", startup_identity))
        if bot_type:
            args.extend(("--bot_type", bot_type))
        if bot_id:
            args.extend(("--bot_id", bot_id))
        if owner_id:
            args.extend(("--owner_id", owner_id))
        if entity_id and entity_type:
            args.extend(("--entity_id", entity_id, "--entity_type", entity_type))
        if stage:
            args.extend(("--stage", stage))
        if admins:
            args.extend(("--admins", admins))

        cmd = shlex.join(str(value) for value in args)
        start_cmd = _admin_background_command(cmd, "/home/admin/start.log")
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=start_cmd,
            timeout_seconds=30,
        )
        logger.info("[_start_baas_sandbox_service] dispatched: bot_uuid=%s", bot_uuid)

        self.dispatch_watchdog(
            bot_uuid=bot_uuid,
            client_id=client_id,
            token=token,
            startup_identity=startup_identity,
        )

    def dispatch_watchdog(
        self,
        *,
        bot_uuid: str,
        client_id: str,
        token: str,
        startup_identity: str | None,
    ) -> None:
        watchdog_values = [
            "/home/admin/bin/starting_watchdog.sh",
            "--token",
            token,
            "--client_id",
            client_id,
        ]
        if startup_identity:
            watchdog_values.extend(("--startup_identity", startup_identity))
        watchdog_args = shlex.join(watchdog_values)
        watchdog_cmd = _admin_background_command(
            watchdog_args, "/home/admin/logs/starting_watchdog.log"
        )
        self._baas_service.exec_command_on_bot(
            bot_uuid=bot_uuid,
            cmd=watchdog_cmd,
            timeout_seconds=30,
        )
        logger.info("[dispatch_watchdog] startup confirmation dispatched: bot_uuid=%s", bot_uuid)


def _admin_background_command(command: str, log_path: str) -> str:
    """Preserve startup envs and support both root and admin exec transports."""
    body = f"nohup {command} >> {shlex.quote(log_path)} 2>&1 &"
    return _admin_command(body)


def _admin_command(command: str) -> str:
    as_admin = shlex.join(["su", "admin", "-c", command])
    return f'if [ "$(id -un)" = admin ]; then\n{command}\nelse\n{as_admin}\nfi'


def _deserialize_symbol(symbol: str | None) -> list[SynlinkMappingInfo]:
    if not symbol or not isinstance(symbol, str):
        return []
    try:
        return [SynlinkMappingInfo(**item) for item in json.loads(symbol)]
    except Exception as e:
        logger.error("[_deserialize_symbol] Failed: %s", e)
        return []
