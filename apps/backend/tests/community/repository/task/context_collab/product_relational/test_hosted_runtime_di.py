"""Product runtime host-capability assembly and admission behavior."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from injector import Injector

from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import (
    actor_context_scope,
)
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import (
    AgentRef,
    PrincipalRef,
)
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l5_methods.hosted_rollout_capabilities import (
    AdaptiveStageImprovementStrategy,
    BotRepositoryRolloutIdentity,
    SignedOpenApiEvidence,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    CompiledStage,
    SessionRoute,
)
from agentclaw.community.core.task.context_collab.l5_methods.stage_rollout import (
    StageRollout,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile import (
    ProductProfileStatus,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.artifact import (
    Artifact,
    ArtifactContentText,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.acceptance_result import (
    AcceptanceResult,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.contracts import (
    ProductProfileServiceProtocol,
)
from agentclaw.community.core.task.task_runner.client.ports import OpenApiBotPort
from agentclaw.community.core.task.task_discovery.notify_messages_provider import (
    NotifyMessagesProvider,
)
from agentclaw.community.di.modules.task.product_profile_runtime import (
    ProductProfileRuntimeModule,
)


class LiveBots:
    def classify_forum_recipient(self, bot_id):
        assert bot_id == "worker"
        return ("live", "creator")


class FakeBot:
    def __init__(self):
        self.send_and_wait_async = AsyncMock(
            return_value={
                "result": {
                    "content": json.dumps(
                        {
                            "action": "RE_DO",
                            "message": "retry after feedback",
                            "rerun": ["build"],
                            "patches": [],
                            "request_feedback": False,
                        }
                    )
                }
            }
        )

    api_key_prefix = "prefix"


class FakeSender:
    channels = frozenset({"markdown"})

    def send(self, message, *, channel="markdown"):
        return "message-id"


@pytest.mark.asyncio
async def test_hosted_di_admits_an_online_collet_and_saves_business_rows(business):
    injector = Injector([ProductProfileRuntimeModule()], auto_bind=False)
    injector.binder.bind(BotRepository, to=LiveBots())
    injector.binder.bind(OpenApiBotPort, to=FakeBot())
    injector.binder.bind(NotifyMessagesProvider, to=FakeSender())
    injector.binder.bind(DatabasePlugin, to=business.db)
    service = injector.get(ProductProfileServiceProtocol)

    with actor_context_scope(business.owner):
        profile = await service.create_product_profile("coding", business.stages)
        await service.update_product_profile(
            profile.id, status=ProductProfileStatus.ONLINE
        )
        collet = await service.create_product_collet(profile.id, "build a feature")

    assert collet.id > 0
    assert collet.content == "build a feature"


@pytest.mark.asyncio
async def test_stage_improvement_requires_a_typed_bot_proposal():
    bot, identities = FakeBot(), BotRepositoryRolloutIdentity(LiveBots())
    strategy = AdaptiveStageImprovementStrategy(bot, identities)
    stage = StageRollout(
        name="build",
        stage_rollout_id=7,
        status=StatusEnum.RUN_FAILED,
        assignee_type="bot",
        assignee_uuid="worker",
        input="{}",
        session_id="session",
        artifacts=[],
        acceptances=[],
        error_msg="runtime failure",
    )
    spec = CompiledStage(
        "build",
        (AgentRef("bot", "worker"),),
        (),
        ("tests pass",),
    )
    snapshot = SimpleNamespace(
        collet=SimpleNamespace(id=1),
        current_rollout_id=2,
        specs=(spec,),
        current=lambda: SimpleNamespace(stages=[stage]),
    )
    proposal = await strategy.propose_adjustment(
        "stage-improvement-agent@1",
        snapshot,
        "please retry",
        SimpleNamespace(principal_type="human", principal_id="creator"),
    )
    assert proposal.type.value == "RE_DO"
    assert proposal.rerun == ("build",)
    assert bot.send_and_wait_async.await_args.kwargs["bot_id"] == "worker:creator"


@pytest.mark.asyncio
async def test_signed_evidence_binds_progress_to_allocated_bot_and_artifacts():
    evidence = SignedOpenApiEvidence()
    route = SessionRoute("openapi_bot", "session", "worker", "creator", "online", "run")
    context = SimpleNamespace(
        source="product_progress",
        actor=PrincipalRef("bot", "worker"),
        accountable_principal_ref=PrincipalRef("human", "creator"),
    )
    acceptance = AcceptanceResult("tests pass", True, "")
    artifact = Artifact(
        "acceptance-evidence", "result", ArtifactContentText("tests passed")
    )

    await evidence.verify(
        "trusted-evidence/v1",
        route,
        ("tests pass",),
        [artifact],
        [acceptance],
        context,
    )

    acceptance = AcceptanceResult("tests pass", True, "")
    with pytest.raises(ContextCollabValidationError, match="artifact evidence"):
        await evidence.verify(
            "trusted-evidence/v1", route, ("tests pass",), [], [acceptance], context
        )


def test_identity_exposes_live_bot_and_owner():
    identity = BotRepositoryRolloutIdentity(LiveBots()).resolve(
        AgentRef("bot", "worker"), None
    )
    assert identity.bot_id == "worker"
    assert identity.owner_id == "creator"
    assert identity.lifecycle_stage == "online"
