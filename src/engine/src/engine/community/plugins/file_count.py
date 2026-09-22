"""Bounded subprocess lifecycle for read-only file counting."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import sys
import threading
import time

from engine.community.kernel.file_count import FileCountError, file_count_request_id

log = logging.getLogger("engine.file_count")
SCAN_TIMEOUT_SECONDS = 120.0
MAX_CONCURRENT_SCANS = 2
_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_SCANS)
_WORKER = str(Path(__file__).with_name("file_count_worker.py"))


async def _stop(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        try:
            process.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=0.5)
        except TimeoutError:
            process.kill()
            await process.wait()
    log.info("engine.file_count.cleanup", extra={
        "status": "reaped", "pid": process.pid, "request_id": file_count_request_id.get(),
    })


async def count_files(
    root: Path, path: str, *, allowed_roots: tuple[Path, ...] | None = None,
) -> dict:
    """Run the shared scanner; omitted roots retain the legacy OpenClaw boundary."""
    started = time.monotonic()
    if not path.strip() or "\x00" in path or len(path) > 4096:
        raise FileCountError("invalid_path")
    # No queue: excess requests immediately release their caller with a stable busy failure.
    if not _SLOTS.acquire(blocking=False):
        raise FileCountError("busy")
    spawn = None
    process = None
    try:
        async with asyncio.timeout(SCAN_TIMEOUT_SECONDS):
            # 以下为安全注释COSEC：固定 Python worker + argv，无 shell、eval 或用户可选命令。
            spawn = asyncio.create_task(asyncio.create_subprocess_exec(
                sys.executable, _WORKER, str(root), path,
                *([json.dumps([str(item) for item in allowed_roots])] if allowed_roots is not None else []),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            ))
            process = await asyncio.shield(spawn)
            output, _ = await process.communicate()
        if process.returncode != 0:
            raise FileCountError("scan_failed")
        result = json.loads(output)
        if "error" in result:
            raise FileCountError(result["error"])
        count = result["file_count"]
        if type(count) is not int or count < 0:
            raise FileCountError("scan_failed")
        log.info("engine.file_count.scan_completed", extra={
            "request_id": file_count_request_id.get(), "file_count": count,
            "timeout_seconds": SCAN_TIMEOUT_SECONDS,
            "skipped_links": result.get("skipped_links", {}),
            "elapsed_ms": int((time.monotonic() - started) * 1000),
        })
        return {"path": path, "file_count": count, "elapsed_ms": int((time.monotonic() - started) * 1000)}
    except TimeoutError:
        raise FileCountError("scan_timeout") from None
    except (OSError, ValueError, KeyError, TypeError):
        raise FileCountError("scan_failed") from None
    finally:
        async def cleanup():
            try:
                target = process
                if target is None and spawn is not None:
                    try:
                        target = await spawn
                    except (OSError, ValueError):
                        return
                if target is not None:
                    await _stop(target)
            finally:
                _SLOTS.release()

        # Keep the slot until the child has exited, even under repeated caller cancellation.
        closing = asyncio.create_task(cleanup())
        cancelled = False
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError:
                cancelled = True
        closing.result()
        if cancelled:
            raise asyncio.CancelledError
