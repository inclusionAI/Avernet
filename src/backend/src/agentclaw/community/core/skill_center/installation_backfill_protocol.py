"""Service API for running the Installation flush deliberately — the backfill.

The Protocol lives here, in the owning core module, so the concrete service can
inherit it without a ``core -> api`` waiver; adapters import it from
``api/installation_backfill_service.py``.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class InstallationBackfillServiceProtocol(Protocol):
    """Converge one Bot's Installation with its SkillSet configuration.

    DB-side only, exactly like the flush it runs: no device is touched and no
    runtime projection is triggered. A Bot converged here still needs a
    projection before its engine sees the change.

    One Bot per call, by design. Choosing which Bots to converge, and at what
    rate, belongs to whoever drives the backfill — this is the tool that call
    invokes, not the driver.
    """

    @abstractmethod
    def backfill_bot(self, *, bot_id: str, owner_id: str) -> None:
        """Flush one exact Bot; raise if it does not exist for this owner.

        The Bot lookup runs under the ambient ``avernet_tenant``, which
        ``AvernetTenantMiddleware`` resolves only for ``/openapi/v1/*`` — every
        other path, the internal API included, is the default tenant.
        """
        ...
