"""Exact persisted session/provider routing, without Event-backed fallback."""

import asyncio
from agentclaw.community.core.errors import InternalError, Forbidden
from agentclaw.community.core.task.task_runner.client.ports import (
    OpenApiBotPort,
    BcsClientPort,
    OpenApiBotError,
)
from agentclaw.community.core.task.task_runner.client.bcs_bot_token_provider import (
    BcsBotTokenProvider,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)


class OpenApiStageSessionMessages:
    def __init__(self, bot: OpenApiBotPort) -> None:
        self.bot = bot

    async def read(self, route, limit):
        if route.provider != "openapi_bot":
            raise Invalid("unsupported persisted session provider")
        try:
            return await self.bot.get_session_messages(
                route.session_id,
                limit=limit,
                bot_id=f"{route.bot_id}:{route.owner_id}",
                lifecycle_stage=route.lifecycle_stage,
            )
        except (TimeoutError, OpenApiBotError) as exc:
            raise InternalError("stage session provider read failed") from exc


class BcsStageSessionMessages:
    def __init__(self, bcs: BcsClientPort, tokens: BcsBotTokenProvider) -> None:
        self.bcs, self.tokens = bcs, tokens

    async def read(self, route, limit):
        if route.provider != "bcs":
            raise Invalid("unsupported persisted session provider")
        token = await asyncio.to_thread(self.tokens.get_token, route.bot_id)
        if not token:
            raise Forbidden("persisted session participant credential is unavailable")
        # Never persist bearer credentials in the stage or request context.
        return await self.bcs.get_session_messages(
            route.session_id, limit=limit, caller_bearer=token
        )


class RoutedStageSessionMessages:
    def __init__(self, providers: dict) -> None:
        self.providers = dict(providers)

    async def read(self, route, limit):
        provider = self.providers.get(route.provider)
        if provider is None:
            raise Invalid("persisted session provider is not installed")
        return await provider.read(route, limit)
