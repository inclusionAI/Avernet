"""Community adapter — the agentswarm service's tower into the creation job.

Implements ``core.agentswarm.service.protocols.BotAuthorizationSource``: the
read side of the bot-create human gate (openspec add-bot-create-human-confirm,
D1/D4). The service composes run detail and the SSE gate snapshots from
``ac_agent_swarm_*`` rows only; the pending authorization's consent URLs and
window live in the durable creation-job payload the manifest seam owns, so
this adapter answers with exactly what that payload freezes — the same
descriptor the host adapter derives for ``bot_status`` — and nothing is
copied into swarm state along the way.

Placement mirrors ``host.py`` (the same bootstrap chain): this concrete
binding reaches ``core.bot_management`` freely because it is a plugin, which
is the one layer the swarm's isolation contract (D17/D35) permits to touch
the creation graph. The service stays behind the duck-typed Protocol and
never imports the manifest seam, the task queue, or anything platform-side.

Never raises: a read that cannot answer returns a with-``None``-fields
descriptor or ``None`` itself (no durable payload for this bot), so an
infrastructure hiccup on the read path degrades the run surface's gate fields
instead of failing the poll that carried it.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agentclaw.community.core.agentswarm.domain.models import BotState
from agentclaw.community.core.agentswarm.service.protocols import (
    BotAuthorizationDescriptor,
)
from agentclaw.community.core.bot_config_manifest.create_job import (
    DEFAULT_CREATE_DEADLINE_SECONDS,
)
from agentclaw.community.core.bot_management.manifest_seam import (
    ManifestCreationSeam,
)


class ManifestBotAuthorizationSource:
    """``BotAuthorizationSource`` over the manifest creation seam.

    One method, one read: ``manifest_seam.find_job`` (the same job lookup
    the with-manifest status route uses) against the run's authenticated
    submitter and the agent's bot id, then the descriptor fields straight
    out of the job payload — ``iframe_url`` / ``redirect_url`` as submitted,
    ``deadline`` as ``submitted_at + window_seconds`` in epoch millis
    (both values frozen at enqueue, so the surfaced horizon can never drift
    from the one the creation job itself enforces).
    """

    def __init__(self, *, manifest_seam: ManifestCreationSeam) -> None:
        self._manifest_seam = manifest_seam

    def authorization(
        self, *, owner_id: str, bot_id: str,
    ) -> BotAuthorizationDescriptor | None:
        job = self._manifest_seam.find_job(
            entity_id=(owner_id or "").strip(),
            bot_id=(bot_id or "").strip(),
        )
        if job is None:
            # No durable creation under this (owner, bot) — nothing to
            # surface; the parked agent's descriptor composes with the
            # module's empty spelling at the caller, never a fabricated
            # URL or deadline.
            return None
        payload: dict[str, Any] = job.payload or {}
        return BotAuthorizationDescriptor(
            state=BotState.AWAITING_AUTHORIZATION.value,
            iframe_url=str(payload.get("iframe_url") or ""),
            redirect_url=str(payload.get("redirect_url") or ""),
            deadline=_window_deadline(payload),
        )


def _window_deadline(payload: dict[str, Any]) -> str:
    """The authorization horizon as the payload's own ISO spelling.

    The arithmetic mirrors the creation job's own expiry check (submitted_at
    plus window_seconds, both frozen in the payload at enqueue) so the
    deadline surfaced on the run is the one the job actually enforces — an
    unreadable ``submitted_at`` reads as the module's empty spelling rather
    than raising, the same fail-soft shape the job's own leave-it-live
    fallback takes.
    """
    submitted = payload.get("submitted_at")
    window = payload.get("window_seconds") or DEFAULT_CREATE_DEADLINE_SECONDS
    try:
        started = datetime.fromisoformat(str(submitted))
    except (TypeError, ValueError):
        return ""
    return (started + timedelta(seconds=int(window))).isoformat()
