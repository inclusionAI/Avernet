"""Derive-on-read: compute a bot's live copy status from existing apply+device state.

`ac_bot_copy.status` is a ledger column written INITIAL at submission and not
CAS-driven (no completion handler under Route A). The FE-facing `copy_state`
field reflects the operation's *current* progress by joining the dst bot's
`ac_bot_config_manifest_apply` records (the two creation-half applies, by
trigger) with `ac_bots.status` — all existing rows from the manifest-creation
flow copy rides on. No new event machinery, no status CAS.

Discrimination of the two applies is by `record.trigger`
(``CREATE_PRE_CONTAINER_TRIGGER`` / ``CREATE_ON_CONTAINER_TRIGGER``) so a
pre_container FAILED is never mistaken for an on_container completion — the
any-FAILED check fires before the on_container states are read.
"""

from __future__ import annotations

from typing import Sequence

from agentclaw.community.core.apply_engine.outcomes import ApplyStatus
from agentclaw.community.core.apply_engine.triggers import (
    CREATE_ON_CONTAINER_TRIGGER,
    CREATE_PRE_CONTAINER_TRIGGER,
)
from agentclaw.community.core.bot_copy.types import (
    BotCopyRow,
    BotCopyStateSummary,
    BotCopyStatus,
)

#: The bot row status meaning "device/container is up" — the on_container apply can
#: only have run after this, so convergence requires it.
_ACTIVE = "ACTIVE"

#: A bot that died during provisioning — device allocation in ``bot_service.create_bot``
#: happens *outside* the apply engine (``_do_allocate``), so its failure is not carried
#: by any apply record and is not caught by the ``_is_failed`` gate above. The only
#: signal derive-on-read has is the bot row's own status: once it is FAILED, the copy
#: has failed too — surface FAILED (FE shows "复制失败" + retry) instead of lingering on
#: a provisional "复制中" (FILE_SYNCED / BOT_STARTED).
_FAILED_BOT_STATUSES = frozenset({"FAILED", "FAILED_STATUS", "DELETED"})


def compute_copy_status(
    *,
    copy_row: BotCopyRow | None,
    dst_apply_records: Sequence,  # duck-typed: anything with .trigger + .status
    dst_bot_status: str | None,
) -> BotCopyStatus | None:
    """The live copy status for one dst bot, or ``None`` for a legacy bot.

    ``None`` when there is no `ac_bot_copy` row for this bot (it was not created
    via copy) — the caller surfaces ``copy_state: null`` to FE.
    """
    if copy_row is None:
        return None

    pre = _find_by_trigger(dst_apply_records, CREATE_PRE_CONTAINER_TRIGGER)
    on = _find_by_trigger(dst_apply_records, CREATE_ON_CONTAINER_TRIGGER)

    # Any FAILED apply failed the copy — checked first, so a pre_container FAILED
    # is not masked by an on_container that never ran (or that succeeded later).
    if _is_failed(pre) or _is_failed(on):
        return BotCopyStatus.FAILED

    # A bot whose row has reached a terminal-failure status failed during
    # provisioning — the failure lives in ``bot_service.create_bot`` (``_do_allocate``)
    # outside the apply engine, so neither apply record carries it and the
    # ``_is_failed`` gate above does not fire. Before any branch can mask it as an
    # in-progress provisional state (FILE_SYNCED / BOT_STARTED), short-circuit to
    # FAILED so FE surfaces "复制失败" + retry rather than a stuck "复制中".
    if dst_bot_status in _FAILED_BOT_STATUSES:
        return BotCopyStatus.FAILED

    if pre is None:
        # No pre_container apply record yet: license still pending, or create_job
        # has not reached apply_pre_container. Both surface as AUTH_PENDING to FE
        # (the POST /copy response already returned iframe_url for the internal case).
        return BotCopyStatus.AUTH_PENDING

    if pre.status == ApplyStatus.RUNNING.value:
        return BotCopyStatus.CREATED

    # pre_container terminal (SUCCEEDED / PARTIAL). PARTIAL means some entries
    # did not land but the phase finished; treat it as "pre done, moving on" —
    # the report carries the detail; FE sees FILE_SYNCED / BOT_STARTED next.
    if on is None:
        # pre done, on_container not yet started: provisioning gap. Device already
        # ACTIVE means on_container apply is imminent — BOT_STARTED; else still
        # provisioning, pre done but device not up — CREATED. File sync runs in
        # the on_container phase (after the device is up), so a pre-only state is
        # never FILE_SYNCED: that value names an on_container signal, and the pre
        # terminal here carries no such signal. (A FAILED bot is caught by the
        # early gate above and never reaches here.)
        return BotCopyStatus.BOT_STARTED if dst_bot_status == _ACTIVE else BotCopyStatus.CREATED

    if on.status == ApplyStatus.RUNNING.value:
        # on_container apply running: device is up (on only runs post-ACTIVE).
        return BotCopyStatus.BOT_STARTED

    if on.status in (ApplyStatus.SUCCEEDED.value, ApplyStatus.PARTIAL.value):
        # on_container is terminal: SUCCEEDED fully converged; PARTIAL means some
        # entries did not land but the phase finished (a retry must converge the
        # remainder via detail-page manifest apply, not the copy retry endpoint).
        # Either way, with the dst device ACTIVE the copy itself is converged —
        # the bot is up and usable; the partial remainder is a separate concern.
        # Device not yet ACTIVE means the report outran the device-alive callback
        # — still BOT_STARTED.
        return BotCopyStatus.CONVERGED if dst_bot_status == _ACTIVE else BotCopyStatus.BOT_STARTED

    # on_container otherwise non-terminal: not converged.
    return BotCopyStatus.BOT_STARTED


def _find_by_trigger(records: Sequence, trigger: str):
    return next((r for r in records if r.trigger == trigger), None)


def _is_failed(record) -> bool:
    return record is not None and record.status == ApplyStatus.FAILED.value


def build_copy_state_summary(
    *,
    bot_copy_repo,
    apply_repo,
    env: str,
    entity_id: str,
    bot_id: str,
    bot_status: str | None,
    recent_limit: int = 10,
) -> BotCopyStateSummary | None:
    """The `copy_state` for one bot, or ``None`` for a legacy (non-copy) bot.

    Joins the `ac_bot_copy` ledger row (by dst_bot_id) with the dst's recent
    `ac_bot_config_manifest_apply` records, then `compute_copy_status` derives
    the live status. Returns ``None`` when there is no copy row — the caller
    surfaces `copy_state: null` to FE.

    `apply_repo.recent(env, entity_id, bot_id, limit)` is the existing read that
    returns the dst's newest apply records newest-first; the two creation halves
    (pre_container / on_container) are discriminated by `record.trigger` inside
    `compute_copy_status`.
    """
    copy_row = bot_copy_repo.get_by_dst_bot_id(env=env, dst_bot_id=bot_id)
    if copy_row is None:
        return None
    applies = apply_repo.recent(
        env=env, entity_id=entity_id, bot_id=bot_id, limit=recent_limit
    )
    status = compute_copy_status(
        copy_row=copy_row,
        dst_apply_records=applies,
        dst_bot_status=bot_status,
    )
    # Once the copy has converged the dst is an ordinary bot — drop the
    # copy_state so FE stops showing copy progress / the retry button.
    if status is BotCopyStatus.CONVERGED:
        return None
    return BotCopyStateSummary(
        operation_id=copy_row.operation_id,
        status=status,
        source_bot_id=copy_row.source_bot_id,
    )


__all__ = ["build_copy_state_summary", "compute_copy_status"]
