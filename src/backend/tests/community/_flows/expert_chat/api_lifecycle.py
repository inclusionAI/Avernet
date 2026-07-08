"""expert_chat route-A session flow definitions for singlebox."""
from __future__ import annotations

from tests.community.framework.flow import FlowCase, FlowStep


EXPERT_CHAT_FLOWS: list[FlowCase] = [
    FlowCase(
        name="singlebox-expert-chat-local-session",
        covers=["expert_chat"],
        steps=[
            FlowStep(
                method="POST",
                path="/api/v1/expert-chats",
                body={"bot_id": "{bot_id}", "owner_id": "{owner_id}"},
                expect={"success": True},
            ),
            FlowStep(method="GET", path="/api/v1/expert-chats", expect={"success": True, "data": {"total": 1}}),
            FlowStep(
                method="POST",
                path="/api/v1/expert-chats/{bot_id}/{owner_id}/session",
                expect={"success": True, "data": {"is_new": True}},
                extract={"expert_session": "data.session_key"},
            ),
            FlowStep(
                method="POST",
                path="/api/v1/expert-chats/{bot_id}/{owner_id}/session",
                expect={"success": True, "data": {"is_new": False}},
                extract={"expert_session_reused": "data.session_key"},
            ),
            FlowStep(
                method="DELETE",
                path="/api/v1/expert-chats/{bot_id}/{owner_id}/session",
                expect={"success": True},
            ),
        ],
    )
]
