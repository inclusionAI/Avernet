"""Transaction-local state for installing a Caller credential under its row lock."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class CallerTokenRefresh:
    """Latest locked instance and optional expiry to persist on successful exit.

    Set ``expires_at`` only after runtime installation succeeds. Leaving it unset
    performs no ledger write. Supplied expiry must advance unless the enclosing
    context explicitly forces runtime recovery; forced recovery records the actual
    installed credential even if its expiry is shorter or equal.
    """

    instance: dict[str, Any]
    expires_at: datetime | None = None
