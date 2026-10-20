"""Publish the draft home's CLI directory as an engine-adjacent artifact."""

from collections.abc import Callable
from pathlib import Path
import os
import stat
import tempfile
import time
from typing import Any

from agentclaw.community.core.service_bot.services.build_ignore_rules import (
    extra_root_rules,
    is_excluded,
)
from agentclaw.community.log import get_logger

logger = get_logger()

# These launchers are managed by the runtime rather than the CLI snapshot.
_RUNTIME_MANAGED_LOCAL_BIN_NAMES = (
    "cfuse",
    "cfuse-claude",
    "cfuse-codex",
)


def _validate_home_dirs(home: Path) -> None:
    # COSEC: fixed home metadata directories must not redirect copying outside
    # the selected artifact/home through an existing symbolic link.
    for directory in (home, home / ".local"):
        if directory.is_symlink():
            raise ValueError(f"home artifact directory is a symlink: {directory}")
        if directory.exists() and not directory.is_dir():
            raise NotADirectoryError(f"home artifact path is not a directory: {directory}")


def _symlink_excludes(source_bin: Path, rules: tuple[str, ...]) -> list[str]:
    """Exclude link entries literally so retry deletion also removes old files."""
    excludes = []

    def scan(directory: Path) -> None:
        # COSEC: scandir does not follow links; inspect each entry before descent.
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(source_bin).as_posix()
                if is_excluded(f".local/bin/{relative}", rules) or relative in _RUNTIME_MANAGED_LOCAL_BIN_NAMES:
                    continue
                if entry.is_symlink():
                    # rsync treats backslashes as escapes only in wildcard
                    # patterns. Preserve literal backslashes in plain names.
                    pattern = relative
                    if any(char in pattern for char in "*?["):
                        pattern = "".join("\\" + char if char in "\\*?[]" else char for char in pattern)
                    excludes.append(pattern)
                    logger.info("service_bot.local_bin.symlink.skip source=%s relative=%s symlink_policy=skip", path, relative)
                elif entry.is_dir(follow_symlinks=False):
                    scan(path)

    scan(source_bin)
    return excludes


def sync_local_bin_artifact(
    *,
    source_home: Path,
    target_dir: Path,
    device_id: str,
    version: str,
    build_ignore_paths: tuple[str, ...],
    run_command: Callable[..., Any],
    enabled: bool = True,
) -> None:
    """Copy only .local/bin; a present directory must copy successfully.

    The sibling bin snapshot is cleaned independently of the engine migration,
    so an absent/ignored CLI source cannot retain a previous retry's snapshot.
    """
    source_bin = source_home / ".local/bin"
    artifact_home = target_dir.parent
    target_bin = artifact_home / ".local/bin"
    started = time.monotonic()
    fields = "device_id=%s version=%s source=%s target=%s"
    context = (device_id, version, source_bin, target_bin)
    logger.info(
        "service_bot.local_bin.request " + fields + " ignore_paths=%s symlink_policy=skip",
        *context, build_ignore_paths,
    )
    try:
        _validate_home_dirs(artifact_home)
        if target_bin.is_symlink():
            raise ValueError("home artifact bin is a symlink")
        if target_bin.exists():
            if not target_bin.is_dir():
                raise NotADirectoryError("home artifact bin is not a directory")
        reason = "feature_disabled" if not enabled else ""
        if not reason and is_excluded(".local/bin", build_ignore_paths):
            reason = "excluded"
        elif not reason:
            # COSEC: validate every source entry before accessing its child;
            # a linked Home/.local/bin must never redirect the source scan.
            for directory in (source_home, source_home / ".local", source_bin):
                try:
                    mode = directory.lstat().st_mode
                except FileNotFoundError:
                    reason = "source_missing"
                    break
                if stat.S_ISLNK(mode):
                    reason = "source_symlink"
                    logger.info("service_bot.local_bin.symlink.skip source=%s symlink_policy=skip", directory)
                    break
                if not stat.S_ISDIR(mode):
                    raise NotADirectoryError(f"draft CLI path is not a directory: {directory}")
        if reason:
            if target_bin.exists():
                # Only the validated bin snapshot is synchronized against an
                # empty source; the version root and other Home data stay put.
                logger.info(
                    "service_bot.local_bin.cleanup.request " + fields
                    + " reason=%s cleanup_method=rsync",
                    *context, reason,
                )
                with tempfile.TemporaryDirectory(prefix="service-bot-cli-empty-") as empty_dir:
                    run_command(
                        cmd=[
                            # Empty-source cleanup needs deletion only; do not
                            # apply the temporary directory's mode/owner to bin.
                            "sudo", "rsync", "-r", "--delete", "--delete-excluded",
                            f"{empty_dir}/", f"{target_bin}/",
                        ],
                        command_name="rsync local bin artifact cleanup",
                        error_message="rsync local bin artifact cleanup failed",
                    )
                logger.info(
                    "service_bot.local_bin.cleanup.success " + fields
                    + " reason=%s cleanup_method=rsync elapsed_ms=%s",
                    *context, reason, round((time.monotonic() - started) * 1000),
                )
            logger.info(
                "service_bot.local_bin.skip " + fields + " reason=%s elapsed_ms=%s",
                *context, reason, round((time.monotonic() - started) * 1000),
            )
            return
        if not source_bin.is_dir():
            raise NotADirectoryError("draft .local/bin is not a directory")
        target_bin.mkdir(parents=True, exist_ok=True)
        exclude_paths = (
            *extra_root_rules(build_ignore_paths, ".local/bin"),
            *_RUNTIME_MANAGED_LOCAL_BIN_NAMES,
            *_symlink_excludes(source_bin, build_ignore_paths),
        )
        # Skip links and exclude their exact names so --delete-excluded also
        # removes regular files published under those names by an earlier retry.
        run_command(
            cmd=[
                "sudo", "rsync", "-av", "--no-links", "--no-devices", "--no-specials",
                "--delete", "--delete-excluded",
                *(f"--exclude=/{path}" for path in exclude_paths),
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
) -> None:
    """Merge the allowlisted bin snapshot into the draft home without deletion."""
    artifact_home = artifact_dir.parent
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
                *(f"--exclude=/{path}" for path in _RUNTIME_MANAGED_LOCAL_BIN_NAMES),
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
