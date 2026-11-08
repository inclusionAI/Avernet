"""The slice's lost-claim unwind signal (D28).

Lives in its own dependency-free module because two runtime components
now need the type at import time: the handler raises it on fenced state
writes, and the pool raises/catches it when the epoch-fenced attempt
journal rejects a stale slice's write. Moving it out of
``runtime/handler/impl.py`` breaks the old laziness-only cycle — pool
imports store, store imports this module, nothing imports the handler at
module scope. ``SwarmRunHandler`` re-exports it as ``_SliceLost`` so
every existing import path keeps working.
"""
from __future__ import annotations


class SliceLost(Exception):
    """Cooperative abort: the slice's claim was lost mid-flight (D28)."""