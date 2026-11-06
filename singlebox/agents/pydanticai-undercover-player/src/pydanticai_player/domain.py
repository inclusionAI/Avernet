"""Transport-independent player tasks and observable output constraints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

READY_TEXT = "投票开始，请大家投出一票，等待主持人公布结果。"


@dataclass(frozen=True)
class PlayerTask:
    kind: Literal["speech", "vote", "ready", "eulogy"]
    node_id: str
    instruction: str
    public_input: str
    upstream: str
    seat: int
    word: str
    max_chars: int
    allowed_targets: tuple[int, ...] = ()
    allow_abstain: bool = True


@dataclass(frozen=True)
class PlayerAnswer:
    text: str
    requests: int
    input_tokens: int
    output_tokens: int


class Player(Protocol):
    async def answer(self, task: PlayerTask) -> PlayerAnswer: ...


class Speech(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, description="Only the player's final sentence, no reasoning")


class Vote(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    target: int | None = Field(description="A legal seat number; null means explicit abstention")


def speech_error(text: str, task: PlayerTask) -> str | None:
    """Mirror the referee's own-word checks; semantic bluntness is not machine-proven."""
    if not text.strip() or text != text.strip() or "\n" in text or "\r" in text:
        return "Return one non-empty sentence without leading/trailing whitespace."
    word = task.word
    if word and (
        word in text
        or any(word[i : i + 2] in text for i in range(len(word) - 1))
        or all(ch in text for ch in word)
    ):
        return "The sentence reveals the private word or its consecutive/split characters."
    if len(text) > task.max_chars:
        return f"The sentence exceeds {task.max_chars} characters."
    return None


def vote_error(vote: Vote, task: PlayerTask) -> str | None:
    if vote.target is None:
        return None if task.allow_abstain else "Abstention is forbidden for this PK vote."
    if vote.target == task.seat or vote.target not in task.allowed_targets:
        return f"Choose one of the legal seats: {list(task.allowed_targets)}."
    return None
