"""Published request and response models for a bot's acli market CLIs."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.caller_identity.schemas import (
    CallerCallType,
)


class BotCliItem(BaseModel):
    """One CLI the bot is granted."""

    cli_code: str = Field(description="CLI code; for acli tools, the acli market name.")
    cli_name: str = Field(description="Display name.")
    cli_desc: str = Field(description="Description.")
    identity_mode: CallerCallType = Field(
        description="Whose identity the CLI runs with: the bot owner or the chat caller."
    )
    default: bool = Field(
        description="True when the bot's default profile provides this CLI. "
        "A default is re-granted at every start and cannot be removed."
    )


class AcliMarketItem(BaseModel):
    """One tool in the acli market."""

    name: str = Field(description="Market name; pass it as `cli_code` to add the tool.")
    command: str = Field(description="The command the tool installs, e.g. `di`.")
    description: str = Field(description="Description from the market.")
    version: str = Field(description="Latest version in the market.")


class AcliMarketPageResult(BaseModel):
    """One page of acli market search results."""

    items: list[AcliMarketItem] = Field(description="This page of matching tools.")
    total: int = Field(description="Matching tools in the market.")
    has_more: bool = Field(description="Whether a further page exists.")


class AddBotCli(BaseModel):
    """Add one acli market tool to the bot."""

    model_config = ConfigDict(extra="forbid")

    cli_code: str = Field(
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
        description="The tool's acli market name, e.g. `deepinsight-cli` "
        "(not its command name `di`). Market names contain no `/`, so every "
        "granted CLI can be addressed by `DELETE …/clis/{cli_code}`.",
    )


class AcliCliOperation(BaseModel):
    """One add, remove or cancel of a bot runtime's CLI."""

    operation_id: str = Field(description="Read it back at `GET …/cli-operations/{operation_id}`.")
    kind: str = Field(description="`operation` (an add or remove) or `cancel`.")
    action: str = Field(description="`install` or `uninstall`; for a cancel, the one it cancels.")
    cli_code: str = Field(description="The CLI's acli market name.")
    stage: str = Field(description="The runtime: `draft`, `verify` or `online`.")
    status: str = Field(description="`running`, `succeeded`, `failed` or `cancelled`.")
    step: str | None = Field(
        description="While running: `granting`, `installing`, `uninstalling` or `cancelling`."
    )
    error_code: str | None = Field(description="Why it failed, e.g. `install_failed`.")
    error_message: str | None = Field(description="The reason, for people.")


class AddedBotCli(BaseModel):
    """The tool installed and granted."""

    cli_code: str = Field(description="The granted CLI code.")
    command: str = Field(description="The command now runnable in the bot.")
    version: str = Field(description="The installed version.")


__all__ = [
    "AcliMarketItem",
    "AcliMarketPageResult",
    "AcliCliOperation",
    "AddBotCli",
    "AddedBotCli",
    "BotCliItem",
]
