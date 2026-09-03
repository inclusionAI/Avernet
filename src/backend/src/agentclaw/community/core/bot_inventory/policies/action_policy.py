"""Action matrix for Bot inventory cards."""
from __future__ import annotations

from agentclaw.community.core.bot_inventory.types import BotAction, BotInventoryKind, DisplayState


def actions_for(
    *, kind: BotInventoryKind, display_state: DisplayState
) -> tuple[tuple[BotAction, ...], dict[str, str]]:
    disabled: dict[str, str] = {}
    if kind is BotInventoryKind.LOCAL:
        if display_state is DisplayState.LOCAL_RUNNING:
            disabled[BotAction.RUNTIME_LOGS.value] = "not supported in this phase"
            disabled[BotAction.ENGINE_RESTART.value] = "not supported in this phase"
            return (
                (
                    BotAction.VIEW,
                    BotAction.CHAT,
                    BotAction.EDIT,
                    BotAction.RESTART,
                    BotAction.DELETE,
                    BotAction.OPEN_FOLDER,
                ),
                disabled,
            )
        if display_state is DisplayState.LOCAL_OFFLINE:
            disabled[BotAction.CHAT.value] = "device offline"
            disabled[BotAction.EDIT.value] = "device offline"
            return (
                (BotAction.VIEW, BotAction.RESTART, BotAction.DELETE, BotAction.OPEN_FOLDER),
                disabled,
            )
        disabled[BotAction.CHAT.value] = "local bot not ready"
        disabled[BotAction.EDIT.value] = "local bot not ready"
        disabled[BotAction.RESTART.value] = "local bot not ready"
        return ((BotAction.VIEW, BotAction.DELETE), disabled)

    if kind is BotInventoryKind.PERSONAL_CLOUD:
        if display_state is DisplayState.RUNNING:
            # ENGINE_RESTART relays the device engine daemon's process restart
            # (POST /openapi/v1/bots/{bot_id}/engine/restart) — a lighter verb
            # than the container re-provision RESTART names, granted beside it.
            return (
                (
                    BotAction.VIEW,
                    BotAction.CHAT,
                    BotAction.EDIT,
                    BotAction.RESTART,
                    BotAction.ENGINE_RESTART,
                    BotAction.DELETE,
                    BotAction.PASSPORT,
                    BotAction.ENGINE_CONFIG,
                    BotAction.DATA_INIT,
                ),
                disabled,
            )
        if display_state is DisplayState.DORMANT:
            disabled[BotAction.CHAT.value] = "activate first"
            disabled[BotAction.EDIT.value] = "activate first"
            disabled[BotAction.RESTART.value] = "activate first"
            disabled[BotAction.ENGINE_RESTART.value] = "activate first"
            return ((BotAction.VIEW, BotAction.ACTIVATE, BotAction.DELETE), disabled)
        if display_state is DisplayState.FAILED:
            disabled[BotAction.RESTART.value] = "bot provisioning failed"
            disabled[BotAction.ENGINE_RESTART.value] = "bot provisioning failed"
            return ((BotAction.VIEW, BotAction.DELETE), disabled)
        disabled[BotAction.CHAT.value] = "bot not ready"
        disabled[BotAction.EDIT.value] = "bot not ready"
        disabled[BotAction.RESTART.value] = "bot not ready"
        disabled[BotAction.ENGINE_RESTART.value] = "bot not ready"
        return ((BotAction.VIEW,), disabled)

    return ((BotAction.VIEW,), disabled)
