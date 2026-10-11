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


def sync_local_bin_artifact(
    *,
    source_home: Path,
    target_dir: Path,
    device_id: str,
    version: str,
    build_ignore_paths: tuple[str, ...],
    run_command: Callable[..., Any],
) -> None:
    """Copy only .local/bin; a present directory must copy successfully.

    The primary migration excludes and deletes .local before this operation,
    so an absent/ignored CLI source cannot retain a previous retry's snapshot.
    """
    source_bin = source_home / ".local/bin"
    target_bin = target_dir / ".local/bin"
    started = time.monotonic()
    fields = "device_id=%s version=%s source=%s target=%s"
    context = (device_id, version, source_bin, target_bin)
    logger.info(
        "service_bot.local_bin.request " + fields + " ignore_paths=%s",
        *context, build_ignore_paths,
    )
    try:
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
