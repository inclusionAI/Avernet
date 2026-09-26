from __future__ import annotations

from .. import logger


def progress(message: str, **fields: object) -> None:
    """Emit one structured pipeline progress log entry."""

    logger.info(message, **fields)
