"""Publish the draft home's CLI directory as an engine-adjacent artifact."""

from collections.abc import Callable
from pathlib import Path
import time
from typing import Any

from agentclaw.community.core.service_bot.services.build_ignore_rules import (
    extra_root_rules,
    is_excluded,
)
from agentclaw.community.log import get_logger

logger = get_logger()
HOME_ADMIN_ARTIFACT_ROOT = "home_admin_root"


def _validate_home_dirs(home: Path) -> None:
    # COSEC: fixed home metadata directories must not redirect copying outside
    # the selected artifact/home through an existing symbolic link.
    for directory in (home, home / ".local"):
        if directory.is_symlink():
            raise ValueError(f"home artifact directory is a symlink: {directory}")
        if directory.exists() and not directory.is_dir():
            raise NotADirectoryError(f"home artifact path is not a directory: {directory}")


def sync_local_bin_artifact(
    *,
    source_home: Path,
    target_dir: Path,
    device_id: str,
    version: str,
    build_ignore_paths: tuple[str, ...],
    run_command: Callable[..., Any],
    artifact_root_name: str = HOME_ADMIN_ARTIFACT_ROOT,
) -> None:
    """Copy only .local/bin; a present directory must copy successfully.

    The primary migration excludes and deletes the reserved home root first,
    so an absent/ignored CLI source cannot retain a previous retry's snapshot.
    """
    source_bin = source_home / ".local/bin"
    artifact_home = target_dir / artifact_root_name
    target_bin = artifact_home / ".local/bin"
    started = time.monotonic()
    fields = "device_id=%s version=%s source=%s target=%s"
    context = (device_id, version, source_bin, target_bin)
    logger.info(
        "service_bot.local_bin.request " + fields + " ignore_paths=%s",
        *context, build_ignore_paths,
    )
    try:
        _validate_home_dirs(artifact_home)
        reason = ""
        if is_excluded(".local/bin", build_ignore_paths):
            reason = "excluded"
        elif not source_bin.exists() and not source_bin.is_symlink():
            reason = "source_missing"
        if reason:
            logger.info(
                "service_bot.local_bin.skip " + fields + " reason=%s elapsed_ms=%s",
                *context, reason, round((time.monotonic() - started) * 1000),
            )
            return
        if not source_bin.is_dir():
            raise NotADirectoryError("draft .local/bin is not a directory")
        if target_bin.is_symlink():
            raise ValueError("home artifact bin is a symlink")
        target_bin.mkdir(parents=True, exist_ok=True)
        # CLI installers commonly link into sibling tool directories. Match
        # the existing runtime copy's -L policy to publish standalone files.
        run_command(
            cmd=[
                "sudo", "rsync", "-avL", "--delete", "--delete-excluded",
                *(f"--exclude=/{path}" for path in extra_root_rules(
                    build_ignore_paths, ".local/bin",
                )),
                f"{source_bin}/", f"{target_bin}/",
            ],
            command_name="rsync local bin artifact",
            error_message="rsync local bin artifact failed",
        )
    except Exception as exc:
        logger.error(
            "service_bot.local_bin.failure " + fields + " error_type=%s error=%s elapsed_ms=%s",
            *context, type(exc).__name__, str(exc),
            round((time.monotonic() - started) * 1000),
        )
        raise
    logger.info(
        "service_bot.local_bin.success " + fields + " elapsed_ms=%s",
        *context, round((time.monotonic() - started) * 1000),
    )


def restore_local_bin_artifact(
    *,
    artifact_dir: Path,
    target_home: Path,
    bot_id: str,
    version: int,
    run_command: Callable[..., Any],
    timeout_seconds: float,
    artifact_root_name: str = HOME_ADMIN_ARTIFACT_ROOT,
) -> None:
    """Merge the allowlisted bin snapshot into the draft home without deletion."""
    artifact_home = artifact_dir / artifact_root_name
    source_bin = artifact_home / ".local/bin"
    target_bin = target_home / ".local/bin"
    started = time.monotonic()
    fields = "bot_id=%s version=%s source=%s target=%s"
    context = (bot_id, version, source_bin, target_bin)
    logger.info("service_bot.local_bin.restore.request " + fields, *context)
    try:
        _validate_home_dirs(artifact_home)
        if not source_bin.exists() and not source_bin.is_symlink():
            logger.info(
                "service_bot.local_bin.restore.skip " + fields
                + " reason=source_missing elapsed_ms=%s",
                *context, round((time.monotonic() - started) * 1000),
            )
            return
        if source_bin.is_symlink() or not source_bin.is_dir():
            raise ValueError("home artifact bin must be a directory, not a symlink")
        _validate_home_dirs(target_home)
        if target_bin.is_symlink():
            raise ValueError("draft home bin is a symlink")
        target_bin.mkdir(parents=True, exist_ok=True)
        run_command(
            cmd=[
                "sudo", "rsync", "-avL", "--chown=1000:1000",
                f"{source_bin}/", f"{target_bin}/",
            ],
            command_name="rsync draft restore local bin",
            error_message="restore draft local bin failed",
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        logger.error(
            "service_bot.local_bin.restore.failure " + fields
            + " error_type=%s error=%s elapsed_ms=%s",
            *context, type(exc).__name__, str(exc),
            round((time.monotonic() - started) * 1000),
        )
        raise
    logger.info(
        "service_bot.local_bin.restore.success " + fields + " elapsed_ms=%s",
        *context, round((time.monotonic() - started) * 1000),
    )
