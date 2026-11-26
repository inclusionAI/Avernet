"""Bounded transport contract for private, stateless Bot model inference."""

from typing import Literal

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
        if sum(len(m.content) for m in self.messages) > 70000:
            raise ValueError("model context exceeds limit")
        return self


class CompletionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=250000)
    model: StrictStr = Field(min_length=1, max_length=256)
    tokens: StrictInt = Field(gt=0)
