"""Real SQL + real runtime/message adapters, recording external capability doubles."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
import pytest_asyncio
from .helpers import persist_profile

from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    ActorContext,
    actor_context_scope,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.product_runtime import (
    OwnerProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    ExecutionIdentity,
    Adjustment,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_engine import (
    RolloutEngine,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_worker import (
    RolloutIntentWorker,
)
from agentclaw.community.core.task.context_collab.l5_methods.openapi_rollout_execution import (
    OpenApiRolloutExecution,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    AdjustTypeEnum,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.session_messages import (
    OpenApiStageSessionMessages,
)
from agentclaw.community.core.task.task_runner.client.ports import (
    BotSendResult,
    BotSessionRuntimeStatus,
)

STAGES = """stages:
  - name: analysis
    candidates:
      - type: bot
        uuid: worker-a
    depends_on: []
    acceptance:
      condition: design is complete
  - name: implementation
    candidates:
      - type: bot
        uuid: worker-a
    depends_on: [analysis]
    acceptance:
      - condition: tests pass
"""


class Authorization(OwnerProductRuntimeAuthorization):
    def check_candidate(self, candidate, context, snapshot):
        if candidate.agent_id != "worker-a":
            raise Forbidden("candidate lacks actual participation grant")


class Identities:
    def resolve(self, candidate, context):
        return ExecutionIdentity(candidate.agent_id, "actual-owner", "production")


class Evidence:
    def __init__(self, owner):
        self.owner = owner
        self.verify = AsyncMock(side_effect=self.validate)

    def require(self, ref):
        if ref != "trusted-evidence/v1":
            raise ContextCollabValidationError("unavailable evidence contract")

    async def validate(self, ref, route, conditions, artifacts, acceptances, context):
        self.require(ref)
        if context.actor != self.owner:
            raise Forbidden("not the trusted verifier")
        if any(a.passed for a in acceptances) and not artifacts:
            raise ContextCollabValidationError(
                "successful verification needs real artifact evidence"
            )


@pytest.fixture
def runtime(db):
    owner = ActorContext(PrincipalRef("human", "profile-owner"), "test")
    bot = SimpleNamespace(
        send_message=AsyncMock(),
        get_session_runtime_status=AsyncMock(),
        get_session_messages=AsyncMock(
            return_value=[
                {
                    "id": "m-1",
                    "role": "assistant",
                    "content": "actual message body",
                    "tool_calls": [],
                }
            ]
        ),
        cancel_run=AsyncMock(),
    )
    calls = []
    state = {"value": "running"}

    async def send(**kwargs):
        calls.append(kwargs)
        return BotSendResult(f"run-{len(calls)}", f"provider-session-{len(calls)}")

    async def observe(session_id, **kwargs):
        suffix = session_id.removeprefix("provider-session-")
        return BotSessionRuntimeStatus(
            session_id=session_id,
            found=True,
            run_id=f"run-{suffix}",
            state=state["value"],
            started_at=1,
            last_event_seq=1,
        )

    bot.send_message.side_effect = send
    bot.get_session_runtime_status.side_effect = observe
    evidence = Evidence(owner.actor)
    execution = OpenApiRolloutExecution(bot, Identities(), evidence)
    authorization = Authorization()
    interaction = SimpleNamespace(
        propose=AsyncMock(
            return_value=Adjustment(AdjustTypeEnum.DO_NOTHING, "unchanged")
        )
    )
    repository = SqlProductRuntimeRepository(db)
    service = ProductProfileService(
        repository,
        authorization,
        execution,
        interaction,
        OpenApiStageSessionMessages(bot),
    )
    engine = RolloutEngine(repository, execution, authorization)
    composition = MethodCompositionTemplate(
        (
            MethodTemplate(
                get_profile("Swarm"),
                SwarmStagesPolicy("openapi-bot/v1", "trusted-evidence/v1", "stages"),
            ),
        )
    )
    runtime = SimpleNamespace(
        owner=owner,
        bot=bot,
        calls=calls,
        state=state,
        evidence=evidence,
        execution=execution,
        authorization=authorization,
        interaction=interaction,
        repository=repository,
        service=service,
        engine=engine,
        composition=composition,
        stages=STAGES,
        worker=RolloutIntentWorker(repository, engine, execution),
    )
    with actor_context_scope(owner):
        yield runtime


@pytest_asyncio.fixture
async def created(runtime):
    h = runtime
    h.profile = persist_profile(h, "coding", h.composition, h.stages)
    h.collet = await h.service.create_product_collet(h.profile.id, "build a feature")
    return h
