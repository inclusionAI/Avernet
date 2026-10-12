"""Content-free retrieval diagnostics shared by matching and reranking."""

import json
import logging
from collections.abc import Iterable


def log_stage(logger: logging.Logger, stage: str, **metrics) -> None:
    """Emit one stage summary for text and structured handlers."""
    logger.info(
        "[RETRIEVAL] stage=%s %s", stage,
        json.dumps(metrics, ensure_ascii=False, default=str),
        extra={"retrieval_stage": stage, **metrics},
    )


def log_candidates(
    logger: logging.Logger, stage: str, candidates: Iterable[tuple[str, float]],
    *, level: int = logging.DEBUG,
) -> None:
    """ID/score details, chunked without silently dropping tail IDs.

    Do not pass profile descriptions, payloads or query text. Positions are
    ranks only when the caller supplies a ranked sequence.
    """
    if not logger.isEnabledFor(level):
        return
    batch = []
    for position, (key, score) in enumerate(candidates, 1):
        batch.append({"position": position, "key": key, "score": round(score, 6)})
        if len(batch) == 25:
            logger.log(level, "[RETRIEVAL-CANDIDATES] stage=%s entries=%s", stage, json.dumps(batch, ensure_ascii=False))
            batch = []
    if batch:
        logger.log(level, "[RETRIEVAL-CANDIDATES] stage=%s entries=%s", stage, json.dumps(batch, ensure_ascii=False))


def log_rows(logger: logging.Logger, stage: str, fields: list[str], rows: Iterable[list]) -> None:
    """Compact INFO decision evidence: explicit columns, at most 25 rows per log."""
    if not logger.isEnabledFor(logging.INFO):
        return
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) == 25:
            log_stage(logger, stage, fields=fields, entries=batch)
            batch = []
    if batch:
        log_stage(logger, stage, fields=fields, entries=batch)
