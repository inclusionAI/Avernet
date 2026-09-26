"""The ``trace_carrier`` column's codec — one concern, both directions.

A task carries the trace context of the request that enqueued it (see the
component README's "Request correlation"). The carrier is the tracer's own
serialization, opaque to this component: nothing here reads its keys, and these
two functions are the only places that turn it into a stored string and back.

They live together, and apart from their callers, because they are a **mirror
pair with one shared rule**: a trace is a diagnostic, so neither direction may
ever fail the operation it is part of. Encoding happens inside the insert's
``orm_session()`` and decoding inside the projection ``claim_batch`` runs over a
whole batch — an unguarded raise in either would cost a caller its enqueue or
strand every task in a claimed batch. Splitting the pair across the repository
and the ORM model (where they started) made that shared rule easy to hold on one
side and forget on the other, which is exactly what happened: the encode side
shipped unguarded.

Contrast ``payload``, whose ``json.dumps`` / ``json.loads`` stay deliberately
unguarded in their own callers. A task that cannot describe its *work* must not
run; a task that cannot describe its *trace* simply runs uncorrelated.
"""
from __future__ import annotations

import json
from typing import Optional

from agentclaw.community.log import get_logger

logger = get_logger()


def encode_trace_carrier(carrier: Optional[dict]) -> Optional[str]:
    """Serialize a carrier for storage, degrading to ``None`` if it cannot be.

    ``ensure_ascii=False`` matches the spelling used for ``payload``; a carrier
    is ASCII in practice, but the two should not differ by accident.

    ``TracerPlugin`` documents a carrier as JSON-serializable and both in-tree
    impls return a flat dict of strings, so the guard should never fire. It
    exists because the cost of being wrong is asymmetric: an impl that one day
    returns a ``datetime``, an SDK object, or a cycle would raise *inside* the
    insert's ``orm_session()``, rolling it back, and the exception would
    propagate out of ``TaskQueueService.enqueue`` to fail the work the caller
    actually asked for. A carrier well-formed enough to export but not to
    serialize is the one path that would otherwise reach the database unguarded.
    """
    if not carrier:
        return None
    try:
        return json.dumps(carrier, ensure_ascii=False)
    except (TypeError, ValueError):
        logger.warning(
            "[task_queue] trace_carrier is not JSON-serializable; "
            "enqueueing without correlation",
            exc_info=True,
        )
        return None


def decode_trace_carrier(raw: Optional[str], task_id: Optional[int]) -> Optional[dict]:
    """Deserialize a stored carrier, degrading to ``None`` on anything odd.

    A row whose carrier is malformed — hand-edited, or written by a tracer whose
    format has since changed — must still be claimable. Letting this raise would
    fail ``to_record`` for that row, and ``claim_batch`` projects a whole batch,
    so one bad row would take its entire batch down and keep doing so on every
    poll.
    """
    if not raw:
        return None
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        decoded = None
    if not isinstance(decoded, dict):
        logger.warning(
            "[task_queue] ignoring unreadable trace_carrier on task id=%s", task_id
        )
        return None
    return decoded
