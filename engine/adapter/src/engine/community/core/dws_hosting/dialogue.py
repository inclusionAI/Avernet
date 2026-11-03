"""Read canonical history as context only; never enqueue history for execution."""

import asyncio
from collections.abc import Callable

from engine.community.core.dws_hosting.connection import verify_account
from engine.community.kernel.dws_connection import DwsError, MessageActivity
from engine.community.kernel.dws_dialogue import DialogueMessage, DialogueSnapshot
from engine.community.kernel.dws_hosting import Turn
from engine.community.plugin_api.dws_connection import DwsConnection


class HostingDialogue:
    def __init__(self, connection: DwsConnection, clock_ms: Callable[[], int]):
        self.connection, self.clock_ms = connection, clock_ms

    async def new_owner_reply(
        self, turn: Turn, snapshot: DialogueSnapshot | None, sent_ids: frozenset[str]
    ) -> bool:
        cutoff = max(m.created_at_ms for m in turn.messages)
        now = self.clock_ms()
        if now < cutoff:
            raise DwsError("dialogue_clock_regressed")
        async with asyncio.timeout(15):
            verify_account(turn.binding, await self.connection.authorization_status())
            history = await self.connection.history(
                turn.conversation_id, cutoff, now + 1
            )
        seen = {m.key for m in snapshot.messages} if snapshot is not None else set()
        senders = {turn.binding.account_id, *(m.sender_id for m in turn.messages)}
        changed = False
        activities = (
            *history.activities,
            *(
                MessageActivity(
                    conversation_id=m.conversation_id,
                    message_id=m.message_id,
                    sender_id=m.sender_id,
                    created_at_ms=m.created_at_ms,
                )
                for m in history.messages
            ),
        )
        for message in activities:
            if message.conversation_id != turn.conversation_id:
                raise DwsError("dialogue_history_scope_mismatch")
            if message.sender_id not in senders:
                raise DwsError("dialogue_history_sender_mismatch")
            # COSEC: sender identity comes from canonical DWS history, not message text.
            # Our own confirmed automatic sends are not human takeover activity.
            if (
                message.sender_id == turn.binding.account_id
                and not message.ai_generated
                and cutoff < message.created_at_ms <= now
                and message.message_id not in sent_ids
                and "message:" + message.message_id not in seen
            ):
                changed = True
        if not changed and history.unsupported_count and not history.activity_complete:
            raise DwsError("owner_activity_incomplete")
        return changed

    async def prepare(self, turn: Turn) -> DialogueSnapshot:
        now = self.clock_ms()
        start = max(0, now - 86_400_000)
        try:
            async with asyncio.timeout(30):
                verify_account(
                    turn.binding, await self.connection.authorization_status()
                )
                history = await self.connection.history(
                    turn.conversation_id, start, now + 1
                )
        except TimeoutError:
            raise DwsError("dialogue_history_timeout") from None
        peers = {m.sender_id for m in turn.messages}
        cutoff = max(m.created_at_ms for m in turn.messages)
        messages = {}
        for message in (*history.messages, *turn.messages):
            # COSEC: neither a foreign conversation nor another sender is context.
            if message.conversation_id != turn.conversation_id:
                raise DwsError("dialogue_history_scope_mismatch")
            owner = message.sender_id == turn.binding.account_id
            if not owner and message.sender_id not in peers:
                raise DwsError("dialogue_history_sender_mismatch")
            if not start <= message.created_at_ms <= now:
                continue
            if not owner and message.created_at_ms > cutoff:
                continue
            item = DialogueMessage(
                key="message:" + message.message_id,
                role="owner" if owner else "peer",
                text=message.text[:2000],
                created_at_ms=message.created_at_ms,
                truncated=len(message.text) > 2000,
            )
            previous = messages.get(item.key)
            if previous is not None and previous != item:
                raise DwsError("dialogue_history_conflict")
            messages[item.key] = item
        ordered = sorted(messages.values(), key=lambda m: (m.created_at_ms, m.key))
        return DialogueSnapshot(captured_at_ms=now, messages=tuple(ordered[-40:]))
