"""``script`` → ``BotStartupScriptService``. One row write, and nothing else.

**Apply never triggers the script's execution.** Writing the
``ac_bot_startup_script`` row is the whole of this materialiser: no restart, no
republish, no payload rebuild, no call to anything that would run it. That is
work-items §2.7's boundary — apply records *delivery*, and the container and the
engine answer for execution — and it is the thing most likely to be "helpfully"
broken later by someone adding a restart so the script "takes effect now".

**When the row does execute**, confirmed against the baas path rather than
assumed: ``BaasService._build_create_bot_payload`` re-reads
``ac_bot_startup_script`` on *every* payload it composes and appends it to
``after_create_cmd_hook``; it is reached from ``create_bot`` **and**
``upgrade_bot``; and ``BotService.restart_bot`` releases the current device and
allocates a new one, so a restart composes a fresh payload. ``deploy_config_composer``
states the contract itself — *"a bot's stored script runs on every deployment"*.

So the effect is **deferred, not lost**: a script written by a later manifest
version executes at the next **device provisioning** — create, restart, or
republish — and never re-executes inside a container that is already up.
"""
from __future__ import annotations

from typing import Any, Sequence

from agentclaw.community.api.bot_startup_script_service import (
    BotStartupScriptServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.apply.context import ApplyContext
from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    EntryOutcome,
    EntryResult,
)
from agentclaw.community.core.bot_config_manifest.apply.registry import (
    CategoryPlan,
    Intent,
    Materialiser,
    PlannedEntry,
    ResolveFailure,
    ResolveResult,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestSection
from agentclaw.community.core.bot_config_manifest.schema import placeholders

#: How a script entry names itself in a report. It has no entity key of its own
#: — there is one script — so the section's own name is used.
_IDENTITY = ManifestSection.SCRIPT.value

#: What the response tells a caller about timing. Phrased in the terms the
#: mechanism actually has: the row is delivered now and executed at the next
#: device provisioning, never re-run inside a container already up. "Next start"
#: was the earlier wording and it was ambiguous enough to be misread as "only
#: the first container".
DELIVERY_NOTE = (
    "delivered now; executes at this bot's next device provisioning (create, "
    "restart or republish). Apply does not run it."
)


class ScriptMaterialiser(Materialiser):
    """Writes the bot's startup script row. Runs nothing."""

    construct = ManifestSection.SCRIPT

    def __init__(self, script_service: BotStartupScriptServiceProtocol) -> None:
        self._scripts = script_service

    async def resolve(
        self, ctx: ApplyContext, entries: Sequence[dict[str, Any]]
    ) -> ResolveResult:
        """Substitute ``${BOT_*}`` and re-check that this bot accepts a script.

        The capability is re-asked rather than trusted from the ``PUT`` that
        accepted the document: a bot's engine can change after a manifest is
        stored, and a teclaw or desktop bot gets no start command from the
        shared sequence, so a script written there would never execute.
        """
        if not ctx.capabilities.supports(ManifestSection.SCRIPT):
            return ResolveResult(
                failures=(
                    ResolveFailure(
                        _IDENTITY,
                        ctx.capabilities.reason_for(ManifestSection.SCRIPT),
                    ),
                )
            )

        if not entries:
            # ``script: null`` — declared, and declared to be nothing. The area
            # is emptied, which for one script means the row is removed.
            return ResolveResult(intents=(Intent(_IDENTITY, None),))

        body = entries[0].get("body")
        if not isinstance(body, str):
            return ResolveResult(
                failures=(
                    ResolveFailure(_IDENTITY, "'script.body' must be a string"),
                )
            )

        # One whitelist, one resolver: the write path validates ``${...}`` names
        # against ``ALLOWED_PLACEHOLDERS`` and this substitutes the same set. A
        # second copy of either would be a second answer.
        return ResolveResult(
            intents=(
                Intent(
                    _IDENTITY,
                    placeholders.resolve(
                        body,
                        engine_type=ctx.engine_type,
                        env=ctx.env,
                        tenant=ctx.tenant,
                    ),
                ),
            )
        )

    async def plan(
        self, ctx: ApplyContext, intents: Sequence[Intent]
    ) -> CategoryPlan:
        """Compare against the **substituted** body, never the document text.

        This comparison is the whole of the convergence criterion. Comparing the
        raw document would report ``updated`` on every apply of any document
        using a placeholder, because the stored text and the written text differ
        by construction.
        """
        current = self._scripts.get_body(
            entity_id=ctx.entity_id, bot_id=ctx.bot_id
        )
        desired = intents[0].value if intents else None

        if desired is None:
            # Declared empty. A row that exists is removed; no row is already
            # the declared state.
            if current:
                return CategoryPlan(entries=(), removals=(_IDENTITY,))
            return CategoryPlan(entries=(), removals=())

        if not current:
            outcome = EntryOutcome.CREATED.value
        elif current == desired:
            outcome = EntryOutcome.UNCHANGED.value
        else:
            outcome = EntryOutcome.UPDATED.value
        return CategoryPlan(entries=(PlannedEntry(intents[0], outcome),))

    async def write(
        self, ctx: ApplyContext, plan: CategoryPlan
    ) -> Sequence[EntryResult]:
        """Write the row, or remove it. Nothing else happens here.

        An ``unchanged`` plan performs **no write at all** — the service is not
        called. That absence is what the convergence test asserts; equal output
        would not prove it.
        """
        if plan.removals:
            self._scripts.delete(entity_id=ctx.entity_id, bot_id=ctx.bot_id)
            return ()

        if not plan.entries:
            return ()

        planned = plan.entries[0]
        if planned.outcome == EntryOutcome.UNCHANGED.value:
            return (
                EntryResult(
                    self.construct,
                    planned.intent.identity,
                    EntryOutcome.UNCHANGED,
                    note=DELIVERY_NOTE,
                ),
            )

        self._scripts.put(
            entity_id=ctx.entity_id,
            bot_id=ctx.bot_id,
            script=planned.intent.value,
            # The actor, never the addressed owner: attributing a collaborator's
            # change to the bot's owner is the one thing an audit column must
            # not do.
            modifier=ctx.actor_id,
        )
        return (
            EntryResult(
                self.construct,
                planned.intent.identity,
                EntryOutcome(planned.outcome),
                note=DELIVERY_NOTE,
            ),
        )


__all__ = ["DELIVERY_NOTE", "ScriptMaterialiser"]
