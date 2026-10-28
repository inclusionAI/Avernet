"""BcsManagersPlugin — full-replace delivery of a Bot's manager set.

The outbound port the Backend uses to push a projected
``ManagerSnapshot`` to BCS. The only mutation is ``replace_managers``:
BCS stores exactly the snapshot's manager id list for that
``bcs_bot_id`` — no add/remove patch protocol, no partial states. The
contract stays transport-free here; the HTTP adapter lives in
``plugins/`` and must not leak its wire shapes back.

``BcsManagersError`` classifies a failed delivery without carrying the
raw response body: a stable ``kind`` (transport never sent it / BCS
rejected it / result unknown) plus a provider ``code``. Anything else a
caller needs to know is a higher-level concern.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from agentclaw.community.kernel.bot_manager_sync import (
    DeliveryFailure,
    ManagerSetChange,
    ManagerSnapshot,
)
from agentclaw.community.plugin_api.base import Plugin


class BcsManagersError(Exception):
    """A ``replace_managers`` call could not be confirmed.

    Carries only the stable ``kind``/``code`` pair — never the original
    response body, which may contain internal diagnostics.
    """

    def __init__(self, kind: DeliveryFailure, code: str) -> None:
        super().__init__(f"{kind.value} {code}")
        self._kind = kind
        self._code = code

    @property
    def kind(self) -> DeliveryFailure:
        return self._kind

    @property
    def code(self) -> str:
        return self._code


@runtime_checkable
class BcsManagersPlugin(Plugin, Protocol):
    """Replace one Bot's BCS manager set with the snapshot's list."""

    async def replace_managers(
        self,
        snapshot: ManagerSnapshot,
        *,
        timeout_seconds: float,
    ) -> ManagerSetChange:
        """Deliver the full replace and return BCS's applied diff.

        ``timeout_seconds`` bounds one attempt; no hidden retries. Any
        outcome other than a returned ``ManagerSetChange`` raises the
        ``BcsManagersError`` classification above.
        """
        ...

    async def aclose(self) -> None:
        """Release delivery resources (pooled connections)."""
        ...