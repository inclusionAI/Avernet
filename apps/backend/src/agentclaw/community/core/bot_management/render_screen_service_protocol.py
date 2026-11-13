"""Service API Protocol for bot render-screen records."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence, runtime_checkable

#: One row of the announce/clone row set the copy path serialised, against which
#: the converge classifies create/update/unchanged/remove. Internal value type,
#: not the persisted `RenderScreenRecord`: only the fields the copy needs.
_RendererOutcome = Literal["created", "updated", "unchanged", "removed", "failed"]


@dataclass(frozen=True)
class RenderScreenRow:
    """A render-screen entry as the manifest carries it: ``name`` + ``cdn_url``."""

    name: str
    cdn_url: str


@dataclass(frozen=True)
class RenderScreenCopyRow:
    """The outcome one serialised row converged to against the destination's set.

    ``reason`` is report-safe text and set only when ``outcome == "failed"``.
    """

    name: str
    outcome: _RendererOutcome
    reason: str | None = None


@dataclass(frozen=True)
class RenderScreenCopyResult:
    """The whole render-screen converge: declared entries then removals, in order."""

    rows: tuple[RenderScreenCopyRow, ...] = field(default=())


@runtime_checkable
class RenderScreenServiceProtocol(Protocol):
    """Service API for render-screen CRUD."""

    def list_render_screens(
        self,
        *,
        bot_id: str,
        owner_id: str | None = None,
        current_user_id: str | None = None,
    ) -> list[Any]: ...

    def create_render_screen(self, *args: Any, **kwargs: Any) -> Any: ...

    def update_render_screen(self, *args: Any, **kwargs: Any) -> Any: ...

    def delete_render_screen(self, *, record_id: int) -> None: ...

    def get_render_screen(self, record_id: int) -> Any | None: ...

    def authorize_render_screen_bot(self, *, bot_id: str, user_id: str) -> str: ...

    def authorize_render_screen_record(
        self,
        *,
        record_id: int,
        user_id: str,
    ) -> Any: ...

    def copy_render_screens(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        rows: Sequence[RenderScreenRow],
    ) -> RenderScreenCopyResult:
        """Converge a copy-serialised render-screen set onto one bot.

        The apply engine's ``bot_config`` materialiser calls this. It is an
        **overwrite** converge — the declared ``rows`` replace the
        destination's existing render-screen set: rows whose ``name`` already
        exists are updated in place, new names are created, and names present
        on the destination but no longer in ``rows`` are soft-deleted.

        Unlike the user-facing create path, this does not run the
        per-row duplicate-name/collaborator checks: the rows are the source
        bot's already-valid set and the destination's ownership is taken from
        the apply context, so there is no second author to authorize. The
        lone failure it can report is a backend write error on one row, which
        it surfaces as one ``RenderScreenCopyRow`` with its reason rather than
        raising mid-converge — the caller (the materialiser) composes that
        into the apply report.

        Idempotent on retry: a row whose name already matches an existing
        record's name and ``cdn_url`` is ``unchanged`` and writes nothing, and
        re-applying the same set after a successful converge performs no
        writes at all — the convergence criterion the apply engine rests on.
        """
        ...
