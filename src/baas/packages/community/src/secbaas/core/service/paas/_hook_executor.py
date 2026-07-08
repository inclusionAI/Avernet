"""Bounded ThreadPoolExecutor for async start hook execution.

Creates a shared executor used by DeviceService.start_device() to run
blocking facade.execute_command() calls off the asyncio event loop,
along with graceful shutdown on application exit.
"""

from concurrent.futures import ThreadPoolExecutor

from secbaas.logger import get_logger

logger = get_logger("core-service")

_EXECUTOR: ThreadPoolExecutor | None = None
_MAX_WORKERS = 20


def get_hook_executor() -> ThreadPoolExecutor:
    """Get or create the shared bounded thread pool for hook execution."""
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = ThreadPoolExecutor(max_workers=_MAX_WORKERS)
        logger.info(f"Hook executor created with max_workers={_MAX_WORKERS}")
    return _EXECUTOR


def shutdown_hook_executor(wait: bool = True) -> None:
    """Gracefully shut down the hook executor.

    Called during application shutdown to ensure in-flight hooks complete.
    """
    global _EXECUTOR
    if _EXECUTOR is not None:
        logger.info("Shutting down hook executor...")
        _EXECUTOR.shutdown(wait=wait)
        _EXECUTOR = None
        logger.info("Hook executor shut down")
