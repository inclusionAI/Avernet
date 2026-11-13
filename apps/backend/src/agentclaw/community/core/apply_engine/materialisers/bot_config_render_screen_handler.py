"""The ``render_screen`` handler — converges a copy's declared screens onto dst.

One synthesis point: a serialised render-screen row is exactly the shape the
manifest carries (``name`` + ``cdn_url``), and the
:class:`~agentclaw.community.core.bot_management.render_screen_service_protocol.RenderScreenServiceProtocol`
has a ``copy_render_screens`` method whose contract is one overwrite converge
of that set onto the destination. So the handler is a thin adapter: it builds
:class:`RenderScreenRow` objects from the declared entries and hands the set
to the service, then translates the service's per-row outcomes into the
materialiser's report currency.

The service owns the destination read + create/update/delete and every failure
mode (a soft-delete of a no-longer-declared screen, a write error on one row).
The handler owns nothing about render screens except the row shape — which is
the schema's, imported from the kind table so the two cannot drift. That is
the whole reason the handler is its own module: a future ``task_nav`` handler
will be a parallel file reaching a different service, and the kind registry
files both under their own ``kind``.
"""
from __future__ import annotations

from typing import Any, Sequence

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.materialisers.bot_config_kind_registry import (
    BotConfigKindOutcome,
    BotConfigSyncHandler,
)
from agentclaw.community.core.bot_management.render_screen_service_protocol import (
    RenderScreenRow,
    RenderScreenServiceProtocol,
)


class RenderScreenSyncHandler(BotConfigSyncHandler):
    """Converges a copy's declared render-screen set onto the destination bot."""

    kind = "render_screen"

    def __init__(self, render_screen_service: RenderScreenServiceProtocol) -> None:
        self._service = render_screen_service

    def sync_kind(
        self,
        *,
        ctx: ApplyContext,
        entries: Sequence[Any],
        is_empty: bool,
    ) -> BotConfigKindOutcome:
        rows = tuple(
            RenderScreenRow(
                name=str(entry.get("name", "")),
                cdn_url=str(entry.get("cdn_url", "")),
            )
            for entry in entries
        )
        result = self._service.copy_render_screens(
            bot_id=ctx.bot_id,
            owner_id=ctx.owner_id,
            actor_id=ctx.actor_id,
            rows=rows,
        )
        # Map the service's outcomes to the kind outcome the materialiser
        # composes: ``removed`` is split out into ``removals`` (a removal has
        # no declared row to attach an outcome to), the rest stay on ``entries``
        # in declaration order — which the service already emitted, removals last.
        entry_outcomes: list[tuple[str, str, str | None]] = []
        removals: list[tuple[str, str | None]] = []
        for row in result.rows:
            if row.outcome == "removed":
                removals.append((row.name, row.reason))
            else:
                entry_outcomes.append((row.name, row.outcome, row.reason))
        return BotConfigKindOutcome(
            kind=self.kind,
            entries=tuple(entry_outcomes),
            removals=tuple(removals),
        )


__all__ = ["RenderScreenSyncHandler"]
