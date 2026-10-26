"""Community adapter — the agentswarm service's tower into the creation job.

Implements ``core.agentswarm.service.protocols.BotAuthorizationSource``: the
read side of the bot-create human gate. The service composes run detail and the SSE gate snapshots from
``ac_agent_swarm_*`` rows only; the pending authorization's consent URLs and
window live in the durable creation-job payload the manifest seam owns, while
PENDING itself is a fact of the passport application the facade holds — so this
adapter answers a bot-kind agent's read by asking the facade for the
application's live status and, only while it is actually PENDING inside the
window, freezing the descriptor out of the job payload — the same contract the
host adapter derives for ``bot_status`` — with nothing copied into swarm state
along the way.

Placement mirrors ``host.py`` (the same bootstrap chain): this concrete
binding reaches ``core.bot_management`` freely because it is a plugin, which
is the one layer the swarm's isolation contract permits to touch the creation
graph. The service stays behind the duck-typed Protocol and never imports the
manifest seam, the task queue, or anything platform-side.

Never raises: the creation read and the facade read each fail soft — an
unreadable facade answer reads as ``None`` (a wait, never a verdict, the same
rule the creation job's own status poll follows), so run-surface gate fields
degrade instead of failing the poll that carried them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from agentclaw.community.log import get_logger
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
from agentclaw.community.plugin_api.passport import PassportPlugin

logger = get_logger()


class ManifestBotAuthorizationSource:
    """``BotAuthorizationSource`` over the manifest seam and the passport facade.

    Two reads, mirroring the host adapter's own gate derivation: the
    creation job the manifest seam persists (the only durable home of the
    consent URLs and the frozen window), and the passport facade's
    ``query_auth_status`` for the application's live status plus its
    identifier. The second read is the pending oracle — a job payload
    alone proves nothing (an auto-issued or already-approved creation
    carries the same URLs), and only PENDING inside the window is the
    state a human can still move. ``application_id`` rides the facade
    answer so a consumer can address the decision surface from the run
    read; it degrades to the empty string when the answer names none.
    """

    def __init__(
        self, *, manifest_seam: ManifestCreationSeam,
        passport_plugin: PassportPlugin,
    ) -> None:
        self._manifest_seam = manifest_seam
        self._passport = passport_plugin

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
        status_answer = self._application_status(
            bot_id=(bot_id or "").strip(), owner=(owner_id or "").strip(),
        )
        if status_answer is None or \
                str(status_answer.get("status") or "") != "PENDING":
            # Issued, approved, rejected, or no answer yet: no gate is
            # pending for this creation, regardless of what URLs its
            # payload froze.
            return None
        payload: dict[str, Any] = job.payload or {}
        if _window_elapsed(payload):
            # PENDING past its horizon: the creation job retires within
            # one poll delay and owns the flip; never surface a dead
            # window as an actionable gate.
            return None
        return BotAuthorizationDescriptor(
            state=BotState.AWAITING_AUTHORIZATION.value,
            iframe_url=str(payload.get("iframe_url") or ""),
            redirect_url=str(payload.get("redirect_url") or ""),
            deadline=_window_deadline(payload),
            application_id=str(status_answer.get("application_id") or ""),
        )

    def _application_status(
        self, *, bot_id: str, owner: str,
    ) -> dict[str, Any] | None:
        """The facade's status answer for this (bot, owner), fail-soft.

        Failures — outage, provider without the leg, malformed answer —
        read as ``None`` (a wait, never a verdict), the same fail-soft
        rule the creation job's own status poll follows.
        """
        try:
            return self._passport.query_auth_status(
                bot_id=bot_id, owner_workno=owner,
            )
        except Exception:  # noqa: BLE001 — a wait, not a verdict
            logger.warning(
                "[agentswarm][authorization] passport status read failed "
                "for bot=%s owner=%s (gate read degrades to not-pending)",
                bot_id, owner,
            )
            return None


def _window_elapsed(payload: dict[str, Any]) -> bool:
    """Whether the payload's own window has lapsed; unreadable reads fresh.

    The arithmetic mirrors the creation job's own expiry check
    (``submitted_at`` plus ``window_seconds``, both frozen at enqueue),
    the same horizon the host adapter's descriptor gate uses.
    """
    submitted = payload.get("submitted_at")
    window = payload.get("window_seconds") or DEFAULT_CREATE_DEADLINE_SECONDS
    try:
        started = datetime.fromisoformat(str(submitted))
    except (TypeError, ValueError):
        return False
    return datetime.now() - started > timedelta(seconds=int(window))


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