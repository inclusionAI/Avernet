"""Engine-runtime facts project registered engine routing capabilities."""

import pytest

from agentclaw.community.core.engine_runtime.models import BotFacts


@pytest.mark.parametrize(
    ("active_engine", "requires_caller_chat_instance"),
    [
        ("openclaw", True),
        ("hermes", True),
        ("teclaw", False),
        ("aicoding", True),
        ("claude_code", True),
    ],
)
def test_bot_facts_use_registered_engine_caller_runtime_capability(
    active_engine: str,
    requires_caller_chat_instance: bool,
) -> None:
    facts = BotFacts.from_record(
        {
            "bot_id": "bot-1",
            "owner_id": "owner-1",
            "bot_type": "service",
            "active_engine": active_engine,
            "call_type": "caller",
        },
        bot_id="requested-bot",
        owner_id="requested-owner",
    )

    assert facts.requires_caller_chat_instance is requires_caller_chat_instance
