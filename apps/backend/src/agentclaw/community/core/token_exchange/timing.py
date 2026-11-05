"""Timing for synchronous token-plugin stages dispatched to the default executor."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from time import monotonic
from typing import ParamSpec, TypeVar

from agentclaw.community.log import get_logger

logger = get_logger()
P = ParamSpec("P")
T = TypeVar("T")


async def run_token_io(
    stage: str, plugin: str, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs,
) -> T:
    submitted = monotonic()

    def invoke() -> T:
        started = monotonic()
        try:
            return fn(*args, **kwargs)
        finally:
            logger.info(
                "iam_token_io stage=%s plugin=%s queue_ms=%.1f execution_ms=%.1f",
                stage, plugin, (started - submitted) * 1000, (monotonic() - started) * 1000,
            )

    return await asyncio.to_thread(invoke)
