"""Bot model inference through authenticated Backend callbacks.

This provider is for built-in operator-scoped Runs only. The Bot comes from the
persisted Run, never Worker input; Backend rechecks access and forwards to its
live engine. Only text/model/usage return. There is no chat turn or credential
export and no automatic fallback to another credential/model source.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from avernet_evolution.core.evolution_run.data_scope import current_data_scope
from avernet_evolution.core.evolution_strategy import Completion
from avernet_evolution.plugins.platform_http import BotHttp, PlatformHttpError


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    role: Literal["system", "user", "assistant"]
    content: str


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    messages: list[_Message] = Field(min_length=1, max_length=20)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    max_tokens: int = Field(default=4096, ge=1, le=8192)

    @model_validator(mode="after")
    def bounded_context(self):
        if sum(len(m.content) for m in self.messages) > 70000:
            raise ValueError("model context exceeds limit")
        return self


class _Result(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: StrictStr = Field(min_length=1, max_length=250000)
    model: StrictStr = Field(min_length=1, max_length=256)
    tokens: StrictInt = Field(gt=0)


@dataclass(frozen=True)
class BotModelCompletion(Completion):
    tokens: int


class HttpBotModels:
    def __init__(
        self,
        http: BotHttp,
        *,
        stage: Literal["draft", "online"],
        call_charge_usd: float,
        model: str | None = None,
    ):
        if not isfinite(call_charge_usd) or not 0 < call_charge_usd <= 0.5:
            raise ValueError("Bot model call charge must be positive and at most 0.5 USD")
        if stage not in {"draft", "online"} or (model is not None and not model.strip()):
            raise ValueError("invalid Bot model routing configuration")
        self.http, self.stage, self.call_charge_usd, self.model = http, stage, call_charge_usd, model

    async def complete(self, *, messages: list[Any], model: str | None = None, max_tokens: int = 4096):
        scope = current_data_scope.get()
        if scope is None or not scope.run_id or not scope.parent or not scope.strategy:
            raise PermissionError("Bot model completion requires a verified Run scope")
        if self.model is not None and model is not None and model != self.model:
            raise ValueError("requested model differs from deployment override")
        body = _Request(messages=messages, model=self.model or model, max_tokens=max_tokens)
        result = await self.http.request(
            scope.bot,
            "POST",
            "/models:complete",
            params={"stage": self.stage},
            body=body.model_dump(),
            timeout=210,
        )
        try:
            completion = _Result.model_validate(result)
            if body.model is not None and completion.model != body.model:
                raise ValueError("model mismatch")
        except ValueError:
            raise PlatformHttpError("invalid Bot model completion response") from None
        return BotModelCompletion(completion.text, completion.model, self.call_charge_usd, completion.tokens)

    async def close(self):
        # The shared bridge's client is owned by platform assembly.
        pass
