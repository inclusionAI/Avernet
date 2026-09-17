"""Publish-ignore mutation contract and domain failures."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class ExpectedTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bot_id: str = Field(min_length=1, max_length=256)
    entity_id: str = Field(min_length=1, max_length=256)
    stage: Literal["draft", "verify", "online"]


class Authorization(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timestamp: int
    signature: str = Field(min_length=88, max_length=88)


class PublishIgnoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_target: ExpectedTarget
    operation: Literal["add", "remove"]
    path: str = Field(min_length=1, max_length=4096)
    request_id: str = Field(min_length=1, max_length=128)
    authorization: Authorization


class PublishIgnoreError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code
