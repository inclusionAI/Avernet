"""OpenAPI operations on one Bot's explicit MCP connection overrides."""

import asyncio
from typing import Annotated, Any, cast

from fastapi import Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field, StrictStr

from agentclaw.community.adapters.http.openapi_v1.bot_access import resolve_legacy_bot
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.adapters.http.openapi_v1.schemas_runtime_projection import (
    DesiredStateResult,
    RuntimeProjectionResult,
    desired_state_from,
)
from agentclaw.community.api.mcp_scoped_config_service import (
    MCPScopedConfigServiceProtocol,
)
from agentclaw.community.core.bot_management.addressing import ResolvedBotContext
from agentclaw.community.core.mcp.scoped_config_contract import BotMCPConfigPatch
from agentclaw.community.di import Injected

AddressedBot = Annotated[ResolvedBotContext, Depends(resolve_legacy_bot)]
ServerCode = Annotated[
    str, Path(min_length=1, max_length=256, description="Opaque MCP server code.")
]


class BotMcpConfigWrite(BaseModel):
    """Sparse updates to the Bot’s explicit MCP overrides."""

    model_config = ConfigDict(extra="forbid")

    headers: dict[str, StrictStr | None] = Field(
        default_factory=dict,
        description="Header changes by case-insensitive name. Null values clear individual overrides.",
        repr=False,
    )
    url: StrictStr | None = Field(
        default=None,
        description="Custom HTTP(S) URL. Null clears the override. Omit to preserve it.",
        repr=False,
    )


class BotMcpConfig(BaseModel):
    """Redacted view of one Bot’s explicitly stored MCP overrides."""

    server_code: str = Field(description="Opaque identifier of the MCP server.")
    headers: dict[str, str] = Field(
        description="Explicit Header names with redacted values."
    )
    url: str | None = Field(description="Redacted explicit URL, or null when absent.")


class BotMcpConfigWriteResult(BotMcpConfig):
    """Committed Bot MCP overrides and the outcome of the delivery attempt."""

    desired_state: DesiredStateResult = Field(description="Durable override write result.")
    runtime_projection: RuntimeProjectionResult = Field(
        description="Delivery status for this Bot and MCP."
    )


def _redacted_config(server_code: str, config: dict[str, Any]) -> BotMcpConfig:
    return BotMcpConfig(
        server_code=server_code,
        headers={name: "****" for name in (config.get("headers") or {})},
        url="****" if config.get("url") else None,
    )


@envelope_errors
async def get_bot_mcp_config(
    bot: AddressedBot,
    server_code: ServerCode,
    request: Request,
    service: MCPScopedConfigServiceProtocol = Injected(MCPScopedConfigServiceProtocol),
) -> Envelope[BotMcpConfig]:
    """Read the addressed Bot's explicit, redacted MCP connection overrides."""
    config = await asyncio.to_thread(service.read_bot, bot=bot, server_code=server_code)
    return envelope(_redacted_config(server_code, config), request)


@envelope_errors
async def put_bot_mcp_config(
    bot: AddressedBot,
    server_code: ServerCode,
    credential_body: BotMcpConfigWrite,
    request: Request,
    service: MCPScopedConfigServiceProtocol = Injected(MCPScopedConfigServiceProtocol),
) -> Envelope[BotMcpConfigWriteResult]:
    """Patch the addressed Bot's MCP overrides and project if the MCP is effective."""
    updated = await service.patch_bot(
        bot=bot,
        server_code=server_code,
        patch=cast(BotMCPConfigPatch, credential_body.model_dump(exclude_unset=True)),
    )
    return envelope(
        BotMcpConfigWriteResult(
            **_redacted_config(server_code, updated.config).model_dump(),
            desired_state=desired_state_from({"changed": updated.changed}),
            runtime_projection=RuntimeProjectionResult(
                status=updated.projection_status,
                reason=updated.projection_reason,
            ),
        ),
        request,
    )
