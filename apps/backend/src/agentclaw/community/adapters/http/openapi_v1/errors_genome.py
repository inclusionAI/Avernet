"""Error→(status, fixed message) rows for the genome surface (RSI-03), merged into ``responses.ENVELOPE_ERRORS``.

Fixed messages, never raw exception text; what a caller acts on rides in
``data`` (:func:`genome_error_data`): the reasons of a refusal, or the
current revision of a ref that moved.
"""

from __future__ import annotations

from agentclaw.community.core.bot_genome.errors import (
    ApplyBusy,
    CaptureRefused,
    ContentNotReachable,
    Drifted,
    LimitExceeded,
    MoverNotAllowed,
    NotAppliable,
    NotPromotable,
    PatchRejected,
    PolicyRejected,
    PromotionUnsupported,
    RefConflict,
    RefNotFound,
    RevisionNotFound,
)

GENOME_ENVELOPE_ERRORS: dict[type[Exception], tuple[int, str]] = {
    # ── Order is load-bearing: lookup is the first isinstance match, and
    # PolicyRejected subclasses PatchRejected. ──
    RevisionNotFound: (404, "Not found"),
    RefNotFound: (404, "Not found"),
    ContentNotReachable: (404, "Not found"),
    RefConflict: (409, "Ref moved: re-read and decide"),
    Drifted: (409, "Bot has drifted from its active revision"),
    ApplyBusy: (409, "Another apply is running on this bot; retry when it ends"),
    PolicyRejected: (422, "Policy rejected"),
    PatchRejected: (422, "Patch rejected"),
    CaptureRefused: (422, "Current state cannot be captured"),
    NotAppliable: (422, "Revision cannot be delivered to this bot yet"),
    NotPromotable: (422, "Revision is not promotable"),
    PromotionUnsupported: (422, "Promotion is not supported for this bot yet"),
    LimitExceeded: (413, "Genome content exceeds a size limit"),
    MoverNotAllowed: (403, "This ref is moved by a different actor"),
}


#: Business codes for the rows above that have one: a busy bot answers like the apply route's own busy refusal.
GENOME_ENVELOPE_ERROR_CODES: dict[type[Exception], int] = {
    ApplyBusy: 409109,
}


def genome_error_data(exc: Exception) -> object | None:
    """The actionable half of a genome refusal."""
    if isinstance(exc, (PatchRejected, CaptureRefused, NotAppliable)):
        return {"reasons": [reason.as_dict() for reason in exc.reasons]}
    if isinstance(exc, RefConflict):
        return {"ref": exc.ref, "expected_revision": exc.expected, "current_revision": exc.current}
    if isinstance(exc, Drifted):
        return {"active": exc.active, "observed": exc.observed}
    return None
