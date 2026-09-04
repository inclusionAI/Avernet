"""Logger accessor — mirrors ``secbaas.community.logger.get_logger``.

The per-job id is tracked in a ``ContextVar`` and injected into each
``LogRecord`` as ``job_id`` by ``RequestIdFilter``.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar

__all__ = ["get_logger", "set_job_id"]

_job_id: ContextVar[str] = ContextVar("job_id", default="-")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def set_job_id(job_id: str) -> None:
    _job_id.set(job_id)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.job_id = _job_id.get()
        return True
