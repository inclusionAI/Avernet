"""Publish workflow orchestration service.

Manages Bot lifecycle changes through multi-stage deployment pipelines
with approval gates and rolling updates. Per D-01, D-01a, D-07.
"""

from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from secbaas.community.api.publish_manage import (
    PublishConfig,
)
from secbaas.community.logger import get_logger

logger = get_logger("core-service")

_BOT_PUBLISH_LOCK_EXPIRE_SECONDS = 300


@runtime_checkable
class PublishLockService(Protocol):
    """Minimal lock surface required to serialize publish creation per bot."""

    def try_lock(
        self,
        lock_name: str,
        lock_holder: str | None = ...,
        expire_seconds: int | None = ...,
        block: bool = ...,
        block_timeout: float | None = ...,
    ) -> AbstractContextManager[Any]:
        """Return a context manager yielding a lock object with ``.acquired``."""
        ...


@dataclass
class _NoOpLock:
    acquired: bool = True


class _NoOpLockService:
    """Lock stand-in used when no distributed lock is wired.

    Concurrency safety then rests entirely on the ``baas_bot`` unique key via
    ``try_insert_pending_bot``; the lock only upgrades the concurrent
    same-type UPDATE response from a conflict error to the existing publish.
    """

    @contextmanager
    def try_lock(self, *_args: Any, **_kwargs: Any) -> Any:
        yield _NoOpLock()


def _extra_config_to_publish_config(
    extra_config: dict[str, Any] | None,
) -> PublishConfig | None:
    """Convert extra_config dict to PublishConfig model."""
    if extra_config:
        return PublishConfig.model_validate(extra_config)
    return None


@dataclass
class BatchConfig:
    """Internal batch configuration for stage generation."""

    batch_index: int
    stage: str
    batch_capacity: int
    cooldown_seconds: int
    device_count: int
