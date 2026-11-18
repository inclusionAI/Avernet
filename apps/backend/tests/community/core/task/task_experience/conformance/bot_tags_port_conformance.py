"""Conformance suite for ``BotTagsPort``.

Documented invariants:
  - ``bot_name`` returns the supplied ``bot_id`` for unknown mappings (identity
    fallback). The contract does not require raising on unknown ids.
  - the return type is a plain ``str``.
"""

from __future__ import annotations

from typing import Callable

from agentclaw.community.core.task.task_experience.protocols import BotTagsPort

__all__ = ["assert_bot_tags_port_invariants", "BotTagsFactory"]

BotTagsFactory = Callable[[], BotTagsPort]


def assert_bot_tags_port_invariants(make_bot_tags: BotTagsFactory) -> None:
    tags = make_bot_tags()
    # Identity fallback for unknown bot ids.
    assert tags.bot_name("unknown_123") == "unknown_123"
    assert isinstance(tags.bot_name("x"), str)
    # Repeated calls must remain stable.
    assert tags.bot_name("unknown_123") == "unknown_123"
