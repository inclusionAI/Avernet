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
from agentclaw.community.core.bot_copy.types import BotCopyRow, BotCopyStatus

#: The bot row status meaning "device/container is up" — the on_container apply can
#: only have run after this, so convergence requires it.
_ACTIVE = "ACTIVE"


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
        # provisioning — FILE_SYNCED.
        return BotCopyStatus.BOT_STARTED if dst_bot_status == _ACTIVE else BotCopyStatus.FILE_SYNCED

    if on.status == ApplyStatus.RUNNING.value:
        # on_container apply running: device is up (on only runs post-ACTIVE).
        return BotCopyStatus.BOT_STARTED

    if on.status == ApplyStatus.SUCCEEDED.value:
        # Both applies terminal + device up → converged. Device not yet ACTIVE
        # means the report outran the device-alive callback — still BOT_STARTED.
        return BotCopyStatus.CONVERGED if dst_bot_status == _ACTIVE else BotCopyStatus.BOT_STARTED

    # on_container PARTIAL or otherwise non-terminal: not converged.
    return BotCopyStatus.BOT_STARTED


def _find_by_trigger(records: Sequence, trigger: str):
    return next((r for r in records if r.trigger == trigger), None)


def _is_failed(record) -> bool:
    return record is not None and record.status == ApplyStatus.FAILED.value


__all__ = ["compute_copy_status"]
