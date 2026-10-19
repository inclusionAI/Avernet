"""Versioned Home/.agentclaw snapshots, including relative npm launchers."""

from collections.abc import Callable
from pathlib import Path
import tempfile
import time
from typing import Any

from agentclaw.community.core.service_bot.services.build_ignore_rules import (
    extra_root_rules,
    is_excluded,
)
from agentclaw.community.log import get_logger

logger = get_logger()


def _validate_directories(home: Path) -> None:
    # COSEC: fixed roots must not redirect reads or writes through a link.
    for directory in (home, home / ".agentclaw"):
        if directory.is_symlink():
            raise ValueError(f"agentclaw artifact directory is a symlink: {directory}")
        if directory.exists() and not directory.is_dir():
            raise NotADirectoryError(f"agentclaw artifact path is not a directory: {directory}")


def sync_agentclaw_artifact(
    *,
    source_home: Path,
    target_dir: Path,
    device_id: str,
    version: str,
    build_ignore_paths: tuple[str, ...],
    run_command: Callable[..., Any],
) -> None:
    """Converge the complete CLI snapshot independently of the engine root."""
    source = source_home / ".agentclaw"
    target = target_dir.parent / ".agentclaw"
    started = time.monotonic()
    fields = "device_id=%s version=%s source=%s target=%s"
    context = (device_id, version, source, target)
    logger.info("service_bot.agentclaw.request " + fields + " symlink_policy=preserve", *context)
    try:
        _validate_directories(target_dir.parent)
        _validate_directories(source_home)
        reason = "excluded" if is_excluded(".agentclaw", build_ignore_paths) else ""
        if not reason and not source.exists():
            reason = "source_missing"
        if reason:
            if target.exists():
                logger.info("service_bot.agentclaw.cleanup.request " + fields + " reason=%s", *context, reason)
                with tempfile.TemporaryDirectory(prefix="service-bot-agentclaw-empty-") as empty:
                    run_command(
                        cmd=["sudo", "rsync", "-r", "--delete", "--delete-excluded", f"{empty}/", f"{target}/"],
                        command_name="rsync agentclaw artifact cleanup",
                        error_message="rsync agentclaw artifact cleanup failed",
                    )
                logger.info("service_bot.agentclaw.cleanup.success " + fields + " reason=%s", *context, reason)
            logger.info("service_bot.agentclaw.skip " + fields + " reason=%s elapsed_ms=%s",
                        *context, reason, round((time.monotonic() - started) * 1000))
            return
        target.mkdir(parents=True, exist_ok=True)
        run_command(
            cmd=[
                "sudo", "rsync", "-a", "--no-devices", "--no-specials",
                "--delete", "--delete-excluded",
                *(f"--exclude=/{path}" for path in extra_root_rules(build_ignore_paths, ".agentclaw")),
                f"{source}/", f"{target}/",
            ],
            command_name="rsync agentclaw artifact",
            error_message="rsync agentclaw artifact failed",
        )
    except Exception as exc:
        logger.error("service_bot.agentclaw.failure " + fields + " error_type=%s elapsed_ms=%s",
                     *context, type(exc).__name__, round((time.monotonic() - started) * 1000))
        raise
    logger.info("service_bot.agentclaw.success " + fields + " elapsed_ms=%s",
                *context, round((time.monotonic() - started) * 1000))


def restore_agentclaw_artifact(
    *,
    artifact_dir: Path,
    target_home: Path,
    bot_id: str,
    version: int,
    run_command: Callable[..., Any],
    timeout_seconds: float,
) -> None:
    """Merge the version snapshot into the draft without deleting local data."""
    source = artifact_dir.parent / ".agentclaw"
    target = target_home / ".agentclaw"
    started = time.monotonic()
    fields = "bot_id=%s version=%s source=%s target=%s"
    context = (bot_id, version, source, target)
    logger.info("service_bot.agentclaw.restore.request " + fields, *context)
    try:
        _validate_directories(artifact_dir.parent)
        if not source.exists():
            logger.info("service_bot.agentclaw.restore.skip " + fields + " reason=source_missing elapsed_ms=%s",
                        *context, round((time.monotonic() - started) * 1000))
            return
        _validate_directories(target_home)
        target.mkdir(parents=True, exist_ok=True)
        run_command(
            cmd=["sudo", "rsync", "-a", "--no-devices", "--no-specials",
                 "--chown=1000:1000", f"{source}/", f"{target}/"],
            command_name="rsync draft restore agentclaw",
            error_message="restore draft agentclaw failed",
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        logger.error("service_bot.agentclaw.restore.failure " + fields + " error_type=%s elapsed_ms=%s",
                     *context, type(exc).__name__, round((time.monotonic() - started) * 1000))
        raise
    logger.info("service_bot.agentclaw.restore.success " + fields + " elapsed_ms=%s",
                *context, round((time.monotonic() - started) * 1000))
