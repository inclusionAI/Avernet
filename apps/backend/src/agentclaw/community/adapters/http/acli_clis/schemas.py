"""Request and response schemas for the internal acli CLI API."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class BotCliItem(BaseModel):
    """One CLI in the bot's AgentPass grant."""

    cli_code: str
    cli_name: str
    cli_desc: str
    identity_mode: str
    default: bool


class AddBotCliRequest(BaseModel):
    """Add one acli market tool; ``lock_epoch`` is the edit lock the caller holds."""

    model_config = ConfigDict(extra="forbid")

    cli_code: Annotated[str, Field(min_length=1, max_length=256)]
    lock_epoch: Annotated[int | None, Field(gt=0, strict=True)] = None


class AddedBotCli(BaseModel):
    """The installed and granted tool."""

    cli_code: str
    command: str
    version: str


class RemovedBotCli(BaseModel):
    """The tool was revoked and uninstalled."""

    cli_code: str
    deleted: bool


class AcliMarketItem(BaseModel):
    """One published tool in the acli market."""

    name: str
    command: str
    description: str
    version: str


class AcliMarketPageResponse(BaseModel):
    """One page of market search results."""

    items: list[AcliMarketItem]
    total: int
    has_more: bool


__all__ = [
    "AcliMarketItem",
    "AcliMarketPageResponse",
    "AddBotCliRequest",
    "AddedBotCli",
    "BotCliItem",
    "RemovedBotCli",
]
