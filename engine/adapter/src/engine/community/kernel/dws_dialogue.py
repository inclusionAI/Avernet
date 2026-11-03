"""Bounded real conversation facts, separate from an agent's internal transcript."""

from typing import Annotated, Literal
from pydantic import Field
from engine.community.kernel.dws_hosting import Value


class DialogueMessage(Value):
    key: str
    role: Literal["peer", "owner", "assistant_sent"]
    text: Annotated[str, Field(max_length=2000)]
    created_at_ms: int
    truncated: bool = False


class DialogueSnapshot(Value):
    captured_at_ms: int
    messages: Annotated[tuple[DialogueMessage, ...], Field(max_length=40)] = ()
    limited: bool = True
