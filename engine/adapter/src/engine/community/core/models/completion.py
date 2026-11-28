"""Optional, stateless text inference using engine-owned routing and credentials.

Not chat: no sessions, tools, workspace context or configuration writes. A model
omission selects the current engine default, not a session override. Explicit
model IDs must name a configured provider/model; callers never supply URLs or
credentials. Unsupported auth/provider types fail rather than falling back.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "user", "assistant"]
    content: str


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    messages: list[Message] = Field(min_length=1, max_length=20)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    max_tokens: int = Field(default=4096, ge=1, le=8192)

    @model_validator(mode="after")
    def bounded_context(self):
        if sum(len(message.content) for message in self.messages) > 70000:
            raise ValueError("model context exceeds limit")
        return self


class ModelCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=250000)
    model: StrictStr = Field(min_length=1, max_length=256)
    tokens: StrictInt = Field(gt=0)


@runtime_checkable
class ModelCompletionService(Protocol):
    async def complete_model(self, request: CompletionRequest) -> ModelCompletion:
        """Infer once with engine-owned settings; never return provider secrets."""
        ...
