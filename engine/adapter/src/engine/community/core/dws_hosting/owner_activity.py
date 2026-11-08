"""Fresh human activity supersedes an unsent candidate, without another model call."""

from engine.community.core.dws_hosting.dialogue import HostingDialogue
from engine.community.core.dws_hosting.storage import durable_call
from engine.community.plugin_api.dws_delivery import HostingOutbox
from engine.community.plugin_api.dws_execution import HostingExecutionStore
from engine.community.plugin_api.dws_hosting import HostingStateStore


class HostingOwnerActivity:
    def __init__(
        self,
        state: HostingStateStore,
        executions: HostingExecutionStore,
        outbox: HostingOutbox,
        dialogue: HostingDialogue,
    ):
        self.state, self.executions, self.outbox, self.dialogue = (
            state,
            executions,
            outbox,
            dialogue,
        )

    async def superseded(self, binding_id: str, turn_id: str) -> bool:
        turn = await durable_call(self.state.get_turn, binding_id, turn_id)
        record = await durable_call(self.executions.get, binding_id, turn_id)
        sent_ids = await durable_call(
            self.outbox.sent_message_ids, binding_id, turn.conversation_id
        )
        return await self.dialogue.new_owner_reply(
            turn, record.invocation.dialogue_snapshot, sent_ids
        )
