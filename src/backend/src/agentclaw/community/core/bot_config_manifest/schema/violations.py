"""What a rejected manifest says, and the one exception that carries it.

**Every refusal names the offending entry.** That is the acceptance criterion
this module exists to make structural rather than aspirational: a violation
cannot be constructed without a ``location``, so a rule that forgets to say
*where* does not compile into a message a caller has to bisect their document
against.

``location`` is a path into the document as the caller wrote it —
``manifest.identity[1].type``, ``sources.content.mode`` — not into any internal
representation. A caller reading it should be able to put their cursor on the
line without a mapping table.

``code`` is the stable half. Messages are prose and will be reworded; a client
that wants to branch (a UI highlighting the offending field, a CI job counting
categories of failure) keys on the code. The pair is what makes the error both
readable and machine-usable, which a single free-text message is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Violation:
    """One reason a manifest document was refused."""

    #: Dotted/indexed path into the submitted document. Required.
    location: str
    #: Stable machine-readable reason, ``snake_case``.
    code: str
    #: Human-readable explanation. Names the rule and, where it helps, the value.
    message: str

    def as_dict(self) -> dict[str, str]:
        """The wire shape. Flat and stable — this is public contract."""
        return {"location": self.location, "code": self.code, "message": self.message}


class ManifestValidationError(ValueError):
    """A document was refused, with the full list of reasons.

    **The list, not the first entry.** ``PUT`` is all-or-nothing (W1 acceptance
    criteria): one unsupported category refuses the whole document and nothing
    is written. Refusing on the first violation would make that a queue —
    a caller fixes one thing, resubmits, and learns the next — so validation
    runs every rule it can and reports what it found in one answer.

    Rules that cannot run because an earlier one failed are simply absent: a
    document whose top level will not parse has no entries to check, so the
    answer is the parse failure alone. That is a real limit and is stated here
    rather than discovered — the list is "everything we could determine", not a
    guarantee that a document passing on the second submission was fully
    described by the first.
    """

    def __init__(self, violations: Sequence[Violation]) -> None:
        self.violations: tuple[Violation, ...] = tuple(violations)
        super().__init__(
            f"config manifest rejected: {len(self.violations)} violation(s): "
            + "; ".join(f"{v.location}: {v.message}" for v in self.violations)
        )

    def as_payload(self) -> dict[str, object]:
        """The ``data`` block the public API returns alongside the refusal."""
        return {"violations": [v.as_dict() for v in self.violations]}
