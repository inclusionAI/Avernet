"""The ``task_nav`` handler — clones a copy's declared source-bot
workflow-permission grants onto dst (fire-and-log).

Mirror of :mod:`bot_config_render_screen_handler` in shape only — a thin
adapter that hands each declared entry to a Port and translates the call into
the materialiser's report currency — but deliberately **not** in failure
semantics. Where ``render_screen`` maps the service's per-row outcomes into
the report (a failed row marks the kind ``failed`` and the materialiser aborts
the category's remaining kinds), ``task_nav`` is **fire-and-log**: it POSTs
one clone request per declared source, and whether clawweb cloned, already
covered, or could not be reached, the kind's outcome is ``unchanged`` and the
category is never aborted.

The divergence is the point. A cross-service best-effort copy of a UX-side
permission matrix must not roll back a bot copy that has already provisioned
the destination — a downstream clawweb 5xx or a network blip is logged, not
fatal. The audit trail loses row-level fidelity (the apply report cannot tell
"cloned 4" from "clawweb was down"), which is the cost; the lock is rudyLi's,
recorded in the spec and revisable to a ``status=failed``-without-abort later
without touching the schema or the Port.

The Port (:class:`BotWorkflowPermissionsClonePort`) is the first cross-service
seam in this table — ``render_screen``'s service is in-process. The env switch
lives in the ``clawweb``-qualified :class:`~agentclaw.community.plugin_api.http_client.HttpClient`
the Port injects (prod / pre / the simulator's clawweb / a no-network
``LocalHttpClient`` in test), never here, so the handler is one impl for every
environment (Rule 14).

Clone is idempotent on clawweb's side (an upsert with a widest-rule-wins merge
plus an ``already_covered`` skip), so the handler issues one clone per entry
on every apply and adds no "already converged → noop" guard — the same
overwrite-converge contract ``render_screen`` keeps, only the converge lives
in the service, not the handler. Because clawweb's clone only ever *adds*
grants (it never downgrades or removes), an empty declared list is a no-op,
not a removal: ``task_nav: []`` leaves the destination's grants untouched,
unlike ``render_screen: []`` which clears the destination's screens.
"""
from __future__ import annotations

from typing import Any, Sequence

from agentclaw.community.core.apply_engine.context import ApplyContext
from agentclaw.community.core.apply_engine.materialisers.bot_config_kind_registry import (
    BotConfigKindOutcome,
    BotConfigSyncHandler,
)
from agentclaw.community.core.bot_management.bot_workflow_permissions_clone_port import (
    BotWorkflowPermissionsClonePort,
)
from agentclaw.community.log import get_logger

logger = get_logger()

#: Constants the handler writes — the manifest carries neither, so a document
#: cannot ask for a dry run or a non-exact scope on this surface.
_TASK_NAV_SCOPE = "exact"
_TASK_NAV_DRY_RUN = False


class TaskNavSyncHandler(BotConfigSyncHandler):
    """Fans the declared task-nav source rows out to clawweb clone requests.

    Each entry is one cross-bot reference (a ``type: "bot"`` row carrying the
    source bot's identity); the handler POSTs one clone per entry, target =
    the apply context's destination bot. The Port is injected (Rule 20/21):
    the prod impl reaches clawweb over the ``clawweb`` HttpClient, a test
    injects a no-network double.
    """

    kind = "task_nav"

    def __init__(self, clone_port: BotWorkflowPermissionsClonePort) -> None:
        self._port = clone_port

    def sync_kind(
        self,
        *,
        ctx: ApplyContext,
        entries: Sequence[Any],
        is_empty: bool,
    ) -> BotConfigKindOutcome:
        # ``is_empty`` (a declared ``task_nav: []``) is a no-op here, not a
        # removal: clawweb's clone only adds grants and cannot converge to an
        # empty set, so an empty list leaves the destination untouched.
        entry_outcomes: list[tuple[str, str, str | None]] = []
        for entry in entries:
            source_bot_id = str(entry.get("source_bot_id", ""))
            source_owner_id = str(entry.get("source_owner_id", ""))
            try:
                self._port.clone(
                    source_bot_id=source_bot_id,
                    source_owner_id=source_owner_id,
                    target_bot_id=ctx.bot_id,
                    target_owner_id=ctx.owner_id,
                    scope=_TASK_NAV_SCOPE,
                    dry_run=_TASK_NAV_DRY_RUN,
                )
                logger.info(
                    "[task_nav] clone requested: src=%s/%s -> dst=%s/%s",
                    source_bot_id, source_owner_id, ctx.bot_id, ctx.owner_id,
                )
            except Exception:  # noqa: BLE001 — fire-and-log: never abort the apply
                logger.exception(
                    "[task_nav] clone request failed: src=%s/%s -> dst=%s/%s",
                    source_bot_id, source_owner_id, ctx.bot_id, ctx.owner_id,
                )
            # The outcome is ``unchanged`` whether the clone succeeded, was
            # already covered, or failed and was logged — fire-and-log does
            # not read the response, so every entry converges the same way.
            entry_outcomes.append((source_bot_id, "unchanged", None))
        return BotConfigKindOutcome(
            kind=self.kind,
            entries=tuple(entry_outcomes),
        )


__all__ = ["TaskNavSyncHandler"]
