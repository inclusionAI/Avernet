"""Request/response models for the bot-scoped MCP group."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_SERVER_CODE_DESC = (
    "The MCP server's code, exactly as returned by the marketplace listing — "
    "an opaque, case-sensitive identifier, e.g. 'mcp.example.weather'."
)


class BotMcpServer(BaseModel):
    """One MCP server on a bot, with whether the bot's agent may call it."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server_code": "mcp.example.weather",
                "name": "Weather",
                "description": "Forecasts and current conditions.",
                "active": True,
                "is_default": False,
            }
        }
    )

    server_code: str = Field(description=_SERVER_CODE_DESC)
    name: str = Field(description="The server's display name.")
    description: str | None = Field(
        default=None, description="The server's description, when it has one."
    )
    active: bool = Field(
        description="Whether this bot's agent may currently call the server. A "
        "newly added server is false until activated."
    )
    is_default: bool = Field(
        description="True for a server the engine supplies to every bot. "
        "Defaults can be deactivated and reactivated, but not removed — "
        "removing one answers 409."
    )


class BotMcpServerState(BaseModel):
    """The outcome of an operation that changes a server's state on a bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "server": {
                    "server_code": "mcp.example.weather",
                    "name": "Weather",
                    "description": "Forecasts and current conditions.",
                    "active": True,
                    "is_default": False,
                },
                "changed": True,
            }
        }
    )

    server: BotMcpServer = Field(description="The server's state after the call.")
    changed: bool = Field(
        description="Whether this call changed anything. False when the server "
        "was already in the requested state — still a success, since these "
        "operations are idempotent."
    )


class BotMcpServerAdd(BaseModel):
    """Body for adding a marketplace MCP server to a bot."""

    # Unknown fields are refused rather than ignored, the stance PR #610 took
    # for the config write: a caller who misspells a field learns immediately
    # instead of believing a setting took effect.
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"example": {"server_code": "mcp.example.weather"}},
    )

    server_code: str = Field(min_length=1, description=_SERVER_CODE_DESC)


class BotMcpServerRemoved(BaseModel):
    """The outcome of removing a server from a bot.

    The removed flag genuinely varies: removing a server the bot does not have
    is a success reporting false, so undoing twice answers the same as undoing
    once.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"server_code": "mcp.example.weather", "removed": True}
        }
    )

    server_code: str = Field(description=_SERVER_CODE_DESC)
    removed: bool = Field(
        description="True when the server was taken off the bot. False when it "
        "was not on it — still a success, not an error."
    )
