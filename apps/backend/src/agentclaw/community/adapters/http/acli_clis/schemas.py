"""Request and response schemas for the internal acli CLI API."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.api.acli_cli_service import (
    AcliAction,
    OperationKind,
    OperationStatus,
    OperationStep,
)


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
    """The tool was uninstalled; its grant is kept."""

    cli_code: str
    deleted: bool


class CliOperation(BaseModel):
    """One add, remove or cancel of a bot runtime's CLI."""

    operation_id: str = Field(description="Read it back at `GET …/cli-operations/{operation_id}`.")
    kind: OperationKind = Field(description="`operation` (an add or remove) or `cancel`.")
    action: AcliAction = Field(
        description="`install` (an add) or `uninstall` (a remove); for a cancel, the one it cancels."
    )
    cli_code: str = Field(description="The CLI's acli market name.")
    stage: Literal["draft", "verify", "online"] = Field(
        description="The runtime: `draft`, `verify` or `online`."
    )
    status: OperationStatus = Field(description="`running`, `succeeded`, `failed` or `cancelled`.")
    step: OperationStep | None = Field(
        description="While running: `granting`, `installing`, `uninstalling` or `cancelling` "
        "(an add or remove a cancel is stopping). `null` once it has ended."
    )
    error_code: str | None = Field(
        description="Why it failed or was cancelled, e.g. `install_failed`, `cancelled`."
    )
    error_message: str | None = Field(description="The reason, for people.")


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
    "CliOperation",
    "RemovedBotCli",
]
