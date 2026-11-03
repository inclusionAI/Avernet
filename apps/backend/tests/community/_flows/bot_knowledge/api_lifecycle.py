"""Real current-selection HTTP flow; no publication snapshot prerequisite."""

from tests.community.framework.flow import FlowCase, FlowStep

BOT_KNOWLEDGE_FLOWS = [
    FlowCase(
        name="knowledge-current-enable-read-disable",
        covers=["bot_knowledge"],
        steps=[
            FlowStep(
                method="PUT",
                path="/api/bot-knowledge/bot/kb",
                headers={"x-user-id": "owner"},
                body={
                    "owner_id": "owner",
                    "enabled": True,
                    "expected_revision": 0,
                },
                expect={"enabled": True, "revision": 1},
            ),
            FlowStep(
                method="GET",
                path="/api/bot-knowledge/bot?owner_id=owner",
                headers={"x-user-id": "owner"},
                expect={"items": [{"kb_id": "kb", "enabled": True}]},
            ),
            FlowStep(
                method="PUT",
                path="/api/bot-knowledge/bot/kb",
                headers={"x-user-id": "owner"},
                body={
                    "owner_id": "owner",
                    "enabled": False,
                    "expected_revision": 1,
                },
                expect={"enabled": False, "revision": 2},
            ),
        ],
    )
]
