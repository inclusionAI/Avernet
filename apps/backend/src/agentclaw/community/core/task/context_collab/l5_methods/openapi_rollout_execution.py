"""Real Bot session adapter; identity and evidence remain independently governed."""

import asyncio
from dataclasses import asdict
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
    ContextCollabNotFoundError as Missing,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    RolloutIdentityPort,
    RolloutEvidencePort,
    SessionRoute,
)


class OpenApiRolloutExecution:
    def __init__(
        self,
        bot: OpenApiBotPort,
        identities: RolloutIdentityPort,
        evidence: RolloutEvidencePort,
    ) -> None:
        self.bot, self.identities, self.evidence = bot, identities, evidence

    def require(self, execution_ref, acceptance_ref, stages):
        if execution_ref != "openapi-bot/v1":
            raise Invalid("unsupported Bot execution capability")
        if any(a.agent_type != "bot" for stage in stages for a in stage.candidates):
            raise Invalid("Bot execution does not support non-Bot stage candidates")
        self.evidence.require(acceptance_ref)

    async def start(self, execution_ref, candidate, content, attempt, context):
        if execution_ref != "openapi-bot/v1":
            raise Invalid("unsupported Bot execution capability")
        identity = await asyncio.to_thread(self.identities.resolve, candidate, context)
        if any(type(v) is not str or not v.strip() for v in asdict(identity).values()):
            raise Invalid("actual Bot/owner/runtime identity is incomplete")
        result = await self.bot.send_message(
            bot_id=f"{identity.bot_id}:{identity.owner_id}",
            message=content,
            metadata={
                "lifecycle_stage": identity.lifecycle_stage,
                "product_stage_attempt": attempt,
            },
            message_id=attempt,
        )
        if not result.session_id or not result.run_id:
            raise Invalid("Bot dispatch did not return a real session and run")
        return SessionRoute(
            "openapi_bot",
            result.session_id,
            identity.bot_id,
            identity.owner_id,
            identity.lifecycle_stage,
            result.run_id,
        )

    async def observe(self, route):
        if route.provider != "openapi_bot":
            raise Invalid("unsupported execution provider")
        observed = await self.bot.get_session_runtime_status(
            route.session_id,
            bot_id=f"{route.bot_id}:{route.owner_id}",
            lifecycle_stage=route.lifecycle_stage,
        )
        if not observed.found:
            raise Missing("persisted stage session was not found by its provider")
        if observed.session_id != route.session_id or observed.run_id != route.run_id:
            raise Invalid("provider observation belongs to another run/session")
        return {
            "starting": "RUNNING",
            "running": "RUNNING",
            "final": "RUN_FINISHED",
            "error": "RUN_FAILED",
            "aborted": "RUN_FAILED",
        }[observed.state]

    async def cancel(self, route):
        await self.bot.cancel_run(route.run_id)
        # The existing port acknowledges a request, not observed cancellation.
        return False

    async def verify(
        self, acceptance_ref, route, conditions, artifacts, acceptances, context
    ):
        await self.evidence.verify(
            acceptance_ref, route, conditions, artifacts, acceptances, context
        )
