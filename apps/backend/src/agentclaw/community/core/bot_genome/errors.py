"""Errors of the Genome Registry and promotion.

Plain exceptions, like the Manifest's: the HTTP group maps each one to a
status in ``adapters/http/openapi_v1/errors_genome.py``. ``Reason`` is the
one shape every refusal lists, so a ``422`` body reads the same whether the
schema, the policy or an apply-time predicate refused.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Reason:
    """One refusal: a stable code, where in the record, and a sentence for the caller."""

    code: str
    location: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "location": self.location, "message": self.message}


class GenomeError(Exception):
    """Base of every registry and promotion error."""


class RevisionNotFound(GenomeError):
    """No revision of this bot has that id, sequence number, or ref name."""


class RefNotFound(GenomeError):
    """The bot has no ref of that name (a CAS move that expected one)."""


class RefConflict(GenomeError):
    """The ref no longer points where the caller believed; ``current`` is where it points now."""

    def __init__(self, ref: str, *, expected: str | None, current: str | None) -> None:
        super().__init__(f"ref {ref!r} points at {current!r}, not {expected!r}")
        self.ref = ref
        self.expected = expected
        self.current = current


class PatchRejected(GenomeError):
    """The patch cannot be recorded; ``reasons`` lists every refusal (README §6.4)."""

    def __init__(self, reasons: list[Reason] | tuple[Reason, ...]) -> None:
        self.reasons: tuple[Reason, ...] = tuple(reasons)
        super().__init__("; ".join(f"{r.location}: {r.message}" for r in self.reasons) or "patch rejected")


class PolicyRejected(PatchRejected):
    """A policy cannot be recorded (schema, order, or a gene path not in its canonical spelling)."""


class CaptureRefused(GenomeError):
    """The bot's current state cannot be recorded as a revision; ``reasons`` names each item."""

    def __init__(self, reasons: list[Reason] | tuple[Reason, ...]) -> None:
        self.reasons: tuple[Reason, ...] = tuple(reasons)
        super().__init__("; ".join(f"{r.location}: {r.message}" for r in self.reasons) or "capture refused")


class LimitExceeded(GenomeError):
    """A size or count limit the contract mirrors from the Manifest was exceeded."""


class MoverNotAllowed(GenomeError):
    """The caller is not the designated mover of that ref kind (01-genome.md §5.1)."""


class ContentNotReachable(GenomeError):
    """The digest is not reachable from this bot's revisions or uploads."""


class Drifted(GenomeError):
    """The bot holds something other than ``active``; only an owner promotion may proceed.

    ``observed`` is the revision id the bot's current state reads as (the
    registry captured it to find out); ``active`` is what the ref says.
    """

    def __init__(self, *, active: str, observed: str) -> None:
        super().__init__(f"bot holds {observed!r} while active is {active!r}")
        self.active = active
        self.observed = observed


class NotPromotable(GenomeError):
    """The revision may not be promoted (a candidate the gate has not accepted)."""


class NotAppliable(GenomeError):
    """The delivery seam refused the compiled revision; ``reasons`` says why."""

    def __init__(self, reasons: list[Reason] | tuple[Reason, ...]) -> None:
        self.reasons: tuple[Reason, ...] = tuple(reasons)
        super().__init__("; ".join(r.message for r in self.reasons) or "not appliable")


class ApplyBusy(GenomeError):
    """Another apply holds the bot's apply lock, so the promotion's delivery could not start; nothing moved."""


class PromotionUnsupported(GenomeError):
    """Promotion is not supported for this bot kind yet (service bots)."""
