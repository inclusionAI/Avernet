"""The fetch road's failure vocabulary, and the record a success carries.

Three names, shared by every transport under ``fetch/`` and by the pipeline
above it. They live together because they are one contract: a fetch either
refuses, fails, or hands back a :class:`FetchedObject`.

``FetchRefusedError`` and ``FetchFailedError`` are the classification both
remaining roads raise — :mod:`.object_store` (oss) and :mod:`.git_source`
(git) — and the distinction is load-bearing above them: apply reports a
refusal as a configuration answer and a failure as a source answer, and the
entry classification in ``apply/`` keys off the type.

``FetchedObject`` is the receipt-bearing record the content store's
``store()`` takes. It sits here rather than beside that store because both
fetchers construct one and the pipeline that carries it between them already
imports this module for the errors.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class FetchRefusedError(Exception):
    """The request never left: transport policy refused it.

    A configuration-class answer (refused scheme/address/policy/budget),
    and no internal text rides out — the fetching side reports the rule,
    not the site's or the caller's data.
    """


class FetchFailedError(Exception):
    """The request was attempted and the source failed it.

    Non-2xx terminal statuses, transport failures, and a digest mismatch —
    the last is deliberately this, not a "success with corrupted bytes".
    """


@dataclass(frozen=True)
class FetchedObject:
    """Fetched bytes with their receipt — write-or-hash material, never run."""

    bytes: bytes
    sha256: str
    url: str
    content_type: Optional[str]
    fetched_at: datetime
    size_bytes: int
