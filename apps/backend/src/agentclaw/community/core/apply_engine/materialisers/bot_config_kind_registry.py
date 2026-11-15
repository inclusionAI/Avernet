"""Per-kind apply-time dispatch for ``bot_config`` — the apply half of the kind seam.

``bot_config`` is a mapping-valued Category whose keys are sync kinds
(``render_screen`` in v1, a future ``task_nav``, …), and each kind's entries
have their own shape. The manifest validator's half of the seam lives in
:mod:`bot_config_manifest.schema.bot_config_kinds` — it refuses a kind no
handler resolves at ``PUT``. This module is the apply-time half: a
:class:`BotConfigKindRegistry` maps kind → :class:`BotConfigSyncHandler`, and
the :class:`BotConfigMaterialiser` resolves the whole mapping, dispatching
each declared kind to its handler.

Adding a kind = adding a handler here and a sub-validator in the schema
table — two edits, one surface, the same discipline the bot-file-sync port
registry uses. The registry is keyed by each handler's own ``kind`` attribute
rather than a name written at the bind site, so a handler cannot be registered
under the wrong kind.

A handler's ``sync_kind`` is a single overwrite converge of one kind's declared
entries onto the destination, and it must be **idempotent**: re-applying the
same declared set after a successful converge writes nothing, the convergence
criterion the apply engine rests on. ``is_empty`` distinguishes "this kind is
declared with an empty list" (overwrite to empty — remove all) from "this kind
is absent" (untouched — the materialiser never dispatches it), so a handler
never has to guess whether to clear.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from agentclaw.community.core.apply_engine.context import ApplyContext


@dataclass(frozen=True)
class BotConfigKindOutcome:
    """One kind's converge, in the currency the materialiser flattens to a report.

    ``entries`` are the declared rows, each with an outcome and optional
    report-safe ``reason`` (set only when the outcome is ``"failed"``).
    ``removals`` are the identities present on the destination but no longer
    declared — a removal has no declared row to attach an outcome to, so it is
    listed separately, the same partition ``CategoryPlan`` keeps. ``ok`` is
    false when any entry failed, which the materialiser uses to mark the
    category's other kinds ``skipped`` and abort — the all-or-nothing rule
    ``resolve`` enforces for every category, held here per kind.

    The outcomes are the plain strings of
    :class:`~agentclaw.community.core.apply_engine.outcomes.EntryOutcome`
    plus ``"removed"``, carried as strings rather than the enum so this module
    stays import-light: nothing here imports the apply-engine outcomes, and the
    materialiser translates them to the enum once when it composes the report.
    """

    kind: str
    entries: tuple[tuple[str, str, str | None], ...] = field(default=())
    #: ``(identity, reason)`` — a removal has no outcome string, only why it left.
    removals: tuple[tuple[str, str | None], ...] = field(default=())

    @property
    def ok(self) -> bool:
        """True when nothing failed — no failed entry and no failed removal."""
        if any(outcome == "failed" for _, outcome, _ in self.entries):
            return False
        # A removal's reason is set only when the removal failed; a successful
        # removal carries no reason. Either is enough to mark the kind not-ok,
        # which the materialiser uses to abort the category's remaining kinds.
        return not any(reason is not None for _, reason in self.removals)


@runtime_checkable
class BotConfigSyncHandler(Protocol):
    """One kind's overwrite-converge onto the destination bot.

    Every handler is a complete answer to "make the destination's ``kind`` area
    equal to ``entries``": it reads the destination's current rows for its
    kind, classifies each declared entry, removes the no-longer-declared, and
    reports one :class:`BotConfigKindOutcome`. The materialiser never inspects
    ``entries`` — each kind's entry shape is the handler's own — which is what
    keeps kind knowledge out of the orchestration.

    ``kind`` is both the handler's dispatch key (the registry files it under
    this) and the manifest key the validator accepts (the kind table), so the
    two surfaces name the same string.
    """

    #: The manifest kind this handler converges, e.g. ``"render_screen"``.
    kind: str

    def sync_kind(
        self,
        *,
        ctx: ApplyContext,
        entries: Sequence[Any],
        is_empty: bool,
    ) -> BotConfigKindOutcome:
        """Converge ``entries`` onto the destination's ``kind`` area.

        Args:
            ctx: the apply context of the destination bot — ``bot_id``,
                ``owner_id``, ``actor_id`` are what a handler writes against.
            entries: the declared rows for this kind, in declaration order.
                Each row's shape is the handler's own and opaque to the
                materialiser; ``is_empty`` is true when the list is empty.
            is_empty: the kind was declared but with no entries — overwrite to
                empty (remove every existing row of this kind). Always read
                rather than inferred from ``not entries`` so a handler's
                ``is_empty`` and an empty-but-truthy list mean the same thing.

        Returns:
            the converge's outcome. Never raises on a row write failure —
            surfaces it as one ``"failed"`` entry with a report-safe reason,
            so the materialiser composes the apply report the orchestrator
            expects rather than unwinding mid-converge.
        """
        ...


class NoopBotConfigSyncHandler:
    """The default refusal — an apply path that hit a kind no DI registered a handler for.

    Refuses rather than silently succeeding. The validator refuses the same
    kind at ``PUT``, so a document reaching this handler means the kind set
    registered at apply time drifted from the validator's — a wiring fault the
    refusal makes visible in the apply report instead of converging nothing and
    reporting success.
    """

    kind = ""

    def __init__(self, kind: str | None = None) -> None:
        if kind:
            self.kind = kind

    def sync_kind(self, *, ctx, entries, is_empty) -> BotConfigKindOutcome:
        return BotConfigKindOutcome(
            kind=self.kind,
            entries=tuple(
                (str(entry.get("name", "")), "failed", f"no handler is registered for bot_config kind '{self.kind}'")
                for entry in (entries or ())
            ),
        )


class BotConfigKindRegistry:
    """Resolve a handler per kind; the default is the noop refusal.

    The composition root registers one handler per kind that has a real
    implementation; an empty kind key (a malformed mapping the validator
    already refused) and an unregistered kind both land on the noop refusal,
    the same shape :class:`BotFileSyncRegistry` keeps for ``file_sync``.
    """

    def __init__(self, handlers: Mapping[str, BotConfigSyncHandler]) -> None:
        self._handlers = dict(handlers)

    def resolve(self, kind: str) -> BotConfigSyncHandler:
        handler = self._handlers.get(kind)
        if handler is not None:
            return handler
        return NoopBotConfigSyncHandler(kind=kind)


__all__ = [
    "BotConfigKindOutcome",
    "BotConfigKindRegistry",
    "BotConfigSyncHandler",
    "NoopBotConfigSyncHandler",
]
