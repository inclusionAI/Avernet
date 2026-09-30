from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BotCommonConfigKey(BaseModel):
    bot_id: str = Field(..., min_length=1)
    entity_id: str = Field(..., min_length=1)
    config_key: str = Field(..., min_length=1)


class GetBotCommonConfigRequest(BotCommonConfigKey):
    pass


class CreateBotCommonConfigRequest(BotCommonConfigKey):
    config_value: Any = None


class UpdateBotCommonConfigRequest(BaseModel):
    id: int = Field(..., gt=0)
    config_value: Any = None


class BatchUpsertBotCommonConfigRequest(BaseModel):
    items: list[CreateBotCommonConfigRequest] = Field(..., min_length=1, max_length=500)


class DeleteBotCommonConfigRequest(BaseModel):
    id: int = Field(..., gt=0)
