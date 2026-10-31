"""Published request and response models for a bot's acli market CLIs."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.caller_identity.schemas import (
    CallerCallType,
)
from agentclaw.community.adapters.http.openapi_v1.engine_runtime.enums import RuntimeStage
from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


# The four enums below mirror the closed sets in core/acli_clis/operations.py
# value for value; the adapter converts by value, so a member added there must
# be published here too.


class CliOperationKind(_DocumentedEnum):
    """Whether a CLI operation is an add or remove, or a cancel of one."""

    OPERATION = "operation"
    CANCEL = "cancel"

    __descriptions__ = {
        "operation": "An add (`install`) or a remove (`uninstall`) of a CLI.",
        "cancel": "A cancel of the running add or remove named by `action`.",
    }


class CliOperationAction(_DocumentedEnum):
    """What a CLI operation does to the tool."""

    INSTALL = "install"
    UNINSTALL = "uninstall"

    __descriptions__ = {
        "install": "Add: grant the CLI, then install it in the runtime.",
        "uninstall": "Remove: uninstall the CLI from the runtime; the grant is kept.",
    }


class CliOperationStatus(_DocumentedEnum):
    """Where a CLI operation stands."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    __descriptions__ = {
        "running": "Still in progress; read it again later.",
        "succeeded": "Done; the runtime has the change.",
        "failed": "Ended without the change; `error_code` and `error_message` say why.",
        "cancelled": "Stopped by a cancel; the runtime is as it was before.",
    }


class CliOperationStep(_DocumentedEnum):
    """What a running CLI operation is doing now."""

    GRANTING = "granting"
    INSTALLING = "installing"
    UNINSTALLING = "uninstalling"
    CANCELLING = "cancelling"

    __descriptions__ = {
        "granting": "Granting the CLI to the bot, before installing it.",
        "installing": "Installing the CLI in the runtime.",
        "uninstalling": "Uninstalling the CLI from the runtime.",
        "cancelling": "Stopping the operation and restoring the runtime.",
    }


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
    kind: CliOperationKind = Field(description="An add or remove, or a cancel of one.")
    action: CliOperationAction = Field(
        description="What the operation does; for a cancel, the action it cancels."
    )
    cli_code: str = Field(description="The CLI's acli market name.")
    stage: RuntimeStage = Field(description="The runtime: `draft`, `verify` or `online`.")
    status: CliOperationStatus = Field(description="Where the operation stands.")
    step: CliOperationStep | None = Field(
        description="What a running operation is doing now; `null` once it has ended."
    )
    error_code: str | None = Field(description="Why it failed, e.g. `install_failed`.")
    error_message: str | None = Field(description="The reason, for people.")


class CliOperationConflict(BaseModel):
    """The `409` payload when another add or remove is running in the runtime."""

    operation: AcliCliOperation = Field(
        description="The running operation; poll it, or cancel it, before trying again."
    )


class AddedBotCli(BaseModel):
    """The tool installed and granted."""

    cli_code: str = Field(description="The granted CLI code.")
    command: str = Field(description="The command now runnable in the bot.")
    version: str = Field(description="The installed version.")


__all__ = [
    "AcliMarketItem",
    "AcliMarketPageResult",
    "AcliCliOperation",
    "CliOperationAction",
    "CliOperationConflict",
    "CliOperationKind",
    "CliOperationStatus",
    "CliOperationStep",
    "AddBotCli",
    "AddedBotCli",
    "BotCliItem",
]
