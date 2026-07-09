"""NAS usage statistics service.

Provides concurrent find traversal for disk usage and file count analysis.
Each directory is processed and written to DB immediately upon completion.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from agentclaw.community.di import get_app_injector
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.database import DatabasePlugin

logger = get_logger()


class CooldownError(Exception):
    """Raised when cooldown period has not elapsed."""
    def __init__(self, remaining_minutes: float):
        self.remaining_minutes = remaining_minutes
        super().__init__(f"Cooldown not elapsed, please wait {remaining_minutes:.1f} minutes")


class NasUsageService:
    """NAS usage statistics service.

    Concurrent processing for:
    - Disk usage per top-level directory (MB)
    - File count per top-level directory

    Each directory is processed and written to DB immediately upon completion.
    """

    TARGET_PATH = "/home/admin/.merge_nas"
    DEFAULT_CONCURRENCY = 8  # 默认并发数

    def __init__(self):
        self._disk_usage_task: asyncio.Task | None = None
        self._file_count_task: asyncio.Task | None = None

    @property
    def disk_usage_running(self) -> bool:
        """Check if disk usage analysis is running."""
        return self._disk_usage_task is not None and not self._disk_usage_task.done()

    @property
    def file_count_running(self) -> bool:
        """Check if file count analysis is running."""
        return self._file_count_task is not None and not self._file_count_task.done()

    def check_cooldown(self, cooldown_minutes: int | None = None) -> None:
        """Check if cooldown period has elapsed since last analysis.

        Args:
            cooldown_minutes: Minimum minutes between analyses. If None, skip check.

        Raises:
            CooldownError: If cooldown period has not elapsed.
        """
        if cooldown_minutes is None or cooldown_minutes <= 0:
            return

        db = get_app_injector().get(DatabasePlugin)
        with db.orm_session() as session:
            result = session.execute(
                text("SELECT MAX(gmt_modified) as last_modified FROM ac_nas_usage_info")
            ).fetchone()

        if result and result.last_modified:
            last_modified = result.last_modified
            # 数据库存储的是北京时间 (UTC+8)，需要转换为 UTC
            if last_modified.tzinfo is None:
                from datetime import timedelta
                local_tz_offset = timedelta(hours=8)  # 北京时间 UTC+8
                last_modified = last_modified - local_tz_offset
                last_modified = last_modified.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            elapsed_minutes = (now - last_modified).total_seconds() / 60

            logger.info(f"[cooldown] cooldown_minutes={cooldown_minutes}, db_original={result.last_modified}, db_utc={last_modified}, now={now}, elapsed={elapsed_minutes:.1f}min")

            if elapsed_minutes < cooldown_minutes:
                remaining = cooldown_minutes - elapsed_minutes
                raise CooldownError(remaining)

    def get_recently_updated_dirs(self, skip_within_minutes: int | None = None) -> set[str]:
        """Get directories updated within the specified minutes.

        Args:
            skip_within_minutes: Skip directories updated within this many minutes.

        Returns:
            Set of directory names that should be skipped.
        """
        if skip_within_minutes is None or skip_within_minutes <= 0:
            return set()

        db = get_app_injector().get(DatabasePlugin)
        with db.orm_session() as session:
            result = session.execute(
                text("""
                    SELECT directory_name
                    FROM ac_nas_usage_info
                    WHERE gmt_modified >= NOW() - INTERVAL :minutes MINUTE
                """),
                {"minutes": skip_within_minutes}
            ).fetchall()

        return {row.directory_name for row in result}

    def trigger_disk_usage_analysis(self, skip_within_minutes: int | None = None, cooldown_minutes: int | None = None, concurrency: int = DEFAULT_CONCURRENCY) -> bool:
        """Trigger disk usage analysis in background.

        Args:
            skip_within_minutes: Skip directories updated within this many minutes.
            cooldown_minutes: Re-check cooldown before starting actual work.
            concurrency: Number of concurrent workers. Default 8.

        Returns:
            True if triggered, False if already running.
        """
        if self.disk_usage_running:
            return False

        self._disk_usage_task = asyncio.create_task(
            self._run_disk_usage_analysis(skip_within_minutes, cooldown_minutes, concurrency)
        )
        return True

    def trigger_file_count_analysis(self, skip_within_minutes: int | None = None, cooldown_minutes: int | None = None, concurrency: int = DEFAULT_CONCURRENCY) -> bool:
        """Trigger file count analysis in background.

        Args:
            skip_within_minutes: Skip directories updated within this many minutes.
            cooldown_minutes: Re-check cooldown before starting actual work.
            concurrency: Number of concurrent workers. Default 8.

        Returns:
            True if triggered, False if already running.
        """
        if self.file_count_running:
            return False

        self._file_count_task = asyncio.create_task(
            self._run_file_count_analysis(skip_within_minutes, cooldown_minutes, concurrency)
        )
        return True

    async def _analyze_single_dir_size(self, dir_path: Path, semaphore: asyncio.Semaphore) -> tuple[str, int | None]:
        """Analyze a single directory for disk usage.

        Returns:
            (dir_name, size_mb) or (dir_name, None) if failed.
        """
        async with semaphore:
            dir_name = dir_path.name
            try:
                # du -sm <dir> returns size in MB
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "/usr/bin/du", "-sm", str(dir_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()

                if proc.returncode == 0:
                    line = stdout.decode("utf-8", errors="replace").strip()
                    if line:
                        size_mb = int(line.split()[0])
                        return (dir_name, size_mb)
            except Exception as e:
                logger.warning(f"[disk-usage] Failed to analyze {dir_name}: {e}")

            return (dir_name, None)

    async def _analyze_single_dir_count(self, dir_path: Path, semaphore: asyncio.Semaphore) -> tuple[str, int | None]:
        """Analyze a single directory for file count.

        Returns:
            (dir_name, file_count) or (dir_name, None) if failed.
        """
        async with semaphore:
            dir_name = dir_path.name
            try:
                # find -type f | wc -l
                proc = await asyncio.create_subprocess_exec(
                    "sudo", "/usr/bin/find", str(dir_path), "-type", "f",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()

                if proc.returncode == 0:
                    output = stdout.decode("utf-8", errors="replace").strip()
                    count = len(output.split("\n")) if output else 0
                    return (dir_name, count)
            except Exception as e:
                logger.warning(f"[disk-usage/file-count] Failed to analyze {dir_name}: {e}")

            return (dir_name, None)

    async def _write_size_to_db(self, dir_name: str, size_mb: int):
        """Write a single directory's size to database."""
        db = get_app_injector().get(DatabasePlugin)
        with db.orm_session() as session:
            session.execute(
                text("""
                    INSERT INTO ac_nas_usage_info (directory_name, total_usage_mb, is_delete)
                    VALUES (:name, :size, 0)
                    ON DUPLICATE KEY UPDATE
                        total_usage_mb = VALUES(total_usage_mb),
                        is_delete = 0
                """),
                {"name": dir_name, "size": size_mb}
            )
        logger.debug(f"[disk-usage] Wrote to DB: {dir_name} = {size_mb}M")

    async def _write_count_to_db(self, dir_name: str, file_count: int):
        """Write a single directory's file count to database."""
        db = get_app_injector().get(DatabasePlugin)
        with db.orm_session() as session:
            session.execute(
                text("""
                    INSERT INTO ac_nas_usage_info (directory_name, file_count, is_delete)
                    VALUES (:name, :count, 0)
                    ON DUPLICATE KEY UPDATE
                        file_count = VALUES(file_count),
                        is_delete = 0
                """),
                {"name": dir_name, "count": file_count}
            )
        logger.debug(f"[disk-usage/file-count] Wrote to DB: {dir_name} = {file_count} files")

    async def _run_disk_usage_analysis(self, skip_within_minutes: int | None = None, cooldown_minutes: int | None = None, concurrency: int = DEFAULT_CONCURRENCY):
        """Concurrently analyze each directory and write to DB immediately.

        Args:
            skip_within_minutes: Skip directories updated within this many minutes.
            cooldown_minutes: Re-check cooldown after filtering, before actual work.
            concurrency: Number of concurrent workers. Default 8.
        """
        start_time = asyncio.get_event_loop().time()
        target_path = Path(self.TARGET_PATH)
        semaphore = asyncio.Semaphore(concurrency)

        logger.info(f"[disk-usage] START | target={self.TARGET_PATH} | concurrency={concurrency} | skip_within={skip_within_minutes}m")

        try:
            # Get all top-level directories
            if not target_path.exists():
                logger.error(f"[disk-usage] Target path not found: {target_path}")
                return

            all_dirs = [d for d in target_path.iterdir() if d.is_dir()]
            total_dirs = len(all_dirs)
            logger.info(f"[disk-usage] Found {total_dirs} directories under {self.TARGET_PATH}")

            # Filter out recently updated directories (resume capability)
            if skip_within_minutes and skip_within_minutes > 0:
                skip_dirs = self.get_recently_updated_dirs(skip_within_minutes)
                dirs = [d for d in all_dirs if d.name not in skip_dirs]
                skipped = len(all_dirs) - len(dirs)
                logger.info(f"[disk-usage] Skipped {skipped} directories updated within {skip_within_minutes} minutes, {len(dirs)} to process")
            else:
                dirs = all_dirs
                logger.info(f"[disk-usage] Processing all {len(dirs)} directories (no skip)")

            total = len(dirs)
            if total == 0:
                logger.info(f"[disk-usage] No directories to process, exiting")
                return

            # Re-check cooldown after filtering, before actual work
            if cooldown_minutes and cooldown_minutes > 0:
                try:
                    self.check_cooldown(cooldown_minutes)
                    logger.info(f"[disk-usage] Cooldown check passed, starting analysis")
                except CooldownError as e:
                    logger.warning(f"[disk-usage] Cooldown check failed after filtering: {e.remaining_minutes:.1f} minutes remaining, aborting")
                    return

            completed = 0
            failed = 0

            async def process_one(dir_path: Path):
                nonlocal completed, failed
                dir_name, size_mb = await self._analyze_single_dir_size(dir_path, semaphore)
                if size_mb is not None:
                    await self._write_size_to_db(dir_name, size_mb)
                    completed += 1
                else:
                    failed += 1

            # Process all directories concurrently with semaphore
            await asyncio.gather(*[process_one(d) for d in dirs])

            elapsed = asyncio.get_event_loop().time() - start_time
            logger.info(f"[disk-usage] END | total={total} | success={completed} | failed={failed} | elapsed={elapsed:.1f}s")

        except Exception as e:
            logger.error(f"[disk-usage] Analysis failed: {e}")

    async def _run_file_count_analysis(self, skip_within_minutes: int | None = None, cooldown_minutes: int | None = None, concurrency: int = DEFAULT_CONCURRENCY):
        """Concurrently analyze each directory and write to DB immediately.

        Args:
            skip_within_minutes: Skip directories updated within this many minutes.
            cooldown_minutes: Re-check cooldown after filtering, before actual work.
            concurrency: Number of concurrent workers. Default 8.
        """
        start_time = asyncio.get_event_loop().time()
        target_path = Path(self.TARGET_PATH)
        semaphore = asyncio.Semaphore(concurrency)

        logger.info(f"[disk-usage/file-count] START | target={self.TARGET_PATH} | concurrency={concurrency} | skip_within={skip_within_minutes}m")

        try:
            # Get all top-level directories
            if not target_path.exists():
                logger.error(f"[disk-usage/file-count] Target path not found: {target_path}")
                return

            all_dirs = [d for d in target_path.iterdir() if d.is_dir()]
            total_dirs = len(all_dirs)
            logger.info(f"[disk-usage/file-count] Found {total_dirs} directories under {self.TARGET_PATH}")

            # Filter out recently updated directories (resume capability)
            if skip_within_minutes and skip_within_minutes > 0:
                skip_dirs = self.get_recently_updated_dirs(skip_within_minutes)
                dirs = [d for d in all_dirs if d.name not in skip_dirs]
                skipped = len(all_dirs) - len(dirs)
                logger.info(f"[disk-usage/file-count] Skipped {skipped} directories updated within {skip_within_minutes} minutes, {len(dirs)} to process")
            else:
                dirs = all_dirs
                logger.info(f"[disk-usage/file-count] Processing all {len(dirs)} directories (no skip)")

            total = len(dirs)
            if total == 0:
                logger.info(f"[disk-usage/file-count] No directories to process, exiting")
                return

            # Re-check cooldown after filtering, before actual work
            if cooldown_minutes and cooldown_minutes > 0:
                try:
                    self.check_cooldown(cooldown_minutes)
                    logger.info(f"[disk-usage/file-count] Cooldown check passed, starting analysis")
                except CooldownError as e:
                    logger.warning(f"[disk-usage/file-count] Cooldown check failed after filtering: {e.remaining_minutes:.1f} minutes remaining, aborting")
                    return

            completed = 0
            failed = 0

            async def process_one(dir_path: Path):
                nonlocal completed, failed
                dir_name, file_count = await self._analyze_single_dir_count(dir_path, semaphore)
                if file_count is not None:
                    await self._write_count_to_db(dir_name, file_count)
                    completed += 1
                else:
                    failed += 1

            # Process all directories concurrently with semaphore
            await asyncio.gather(*[process_one(d) for d in dirs])

            elapsed = asyncio.get_event_loop().time() - start_time
            logger.info(f"[disk-usage/file-count] END | total={total} | success={completed} | failed={failed} | elapsed={elapsed:.1f}s")

        except Exception as e:
            logger.error(f"[disk-usage/file-count] Analysis failed: {e}")


# Singleton instance
_nas_usage_service: NasUsageService | None = None


def get_nas_usage_service() -> NasUsageService:
    """Get the singleton NasUsageService instance."""
    global _nas_usage_service
    if _nas_usage_service is None:
        _nas_usage_service = NasUsageService()
    return _nas_usage_service
