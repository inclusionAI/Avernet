"""Runtime persistence/maintenance port. Operations are per trusted binding."""

from contextlib import AbstractAsyncContextManager
from typing import Protocol

from engine.community.kernel.dws_hosting import Binding
from engine.community.kernel.dws_runtime import (
    HostingIssue,
    PendingTurn,
    Resolution,
    RuntimeStatus,
)


class HostingRuntimeStore(Protocol):
    def exclusive(self) -> AbstractAsyncContextManager[None]:
        """Single scheduler per database; held until all workers have stopped."""
        ...

    def status(self, binding: Binding) -> RuntimeStatus: ...

    def issues(
        self, binding: Binding, *, limit: int, offset: int
    ) -> tuple[HostingIssue, ...]:
        """Owner operations projection, without message bodies or credentials."""
        ...

    def prepare_restart(self, binding: Binding, *, now_ms: int) -> None:
        """Under exclusive lease, fence interrupted dispatches; never replay writes."""
        ...

    def pending(self, binding: Binding, *, limit: int) -> tuple[PendingTurn, ...]:
        """Return undispatched claims or saved results missing their outbox."""
        ...

    def maintain(self, binding: Binding, *, now_ms: int) -> None:
        """Bound cleanup; preserve uncertain work, IDs and conversation mappings."""
        ...

    def resolve_manual(
        self, binding: Binding, resolution: Resolution, *, actor_id: str, now_ms: int
    ) -> None:
        """Owner acknowledges responsibility, no resend or invented delivered state.

        Execution resolution requires a prior correlated ended observation by the
        service. Delivery resolution takes the conversation over before unblocking.
        The runtime must be stopped and exclusive lease held by the caller.
        """
        ...
