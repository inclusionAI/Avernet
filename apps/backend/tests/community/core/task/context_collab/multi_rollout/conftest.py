"""Independent SQL connections, real Swarm/Bot adapter and isolated file outputs."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import create_engine

from tests.community.core.task.context_collab.product_profile_service.conftest import (
    runtime as runtime,
)
from agentclaw.community.core.base import Base
from agentclaw.community.core.errors import Forbidden
from agentclaw.community.core.task.context_collab.errors import (
    ContextCollabValidationError as Invalid,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_policy import (
    MultiRolloutMethodPolicy,
    MultiRolloutLimits,
    ParallelBestOf,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_contracts import (
    StrategyDescriptor,
    CandidateScore,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_capabilities import (
    HighestScoreStrategy,
)
from agentclaw.community.core.task.context_collab.l5_methods.multi_rollout_controller import (
    MultiRolloutController,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_engine import (
    RolloutEngine,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_worker import (
    RolloutIntentWorker,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_profile import (
    get_profile,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_policies import (
    SwarmStagesPolicy,
)
from agentclaw.community.core.task.context_collab.l5_methods.method_composition import (
    CompositionRelation,
)
from agentclaw.community.core.task.context_collab.l6_products.contracts import (
    MethodCompositionTemplate,
    MethodTemplate,
    MethodRelationTemplate,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.session_messages import (
    OpenApiStageSessionMessages,
)
from agentclaw.community.core.task.context_collab.l6_products.product_profile_service.service import (
    ProductProfileService,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.profile_publication import (
    ProfilePublisherAllowlist,
)

STAGE = """stages:
  - name: build
    candidates:
      - type: bot
        uuid: worker-a
    depends_on: []
    acceptance:
      - condition: tests pass
"""


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'multi.sqlite'}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def policy(strategy=None, **limits):
    strategy = strategy or ParallelBestOf(4, 2, "highest-score@1")
    defaults = dict(
        max_rollouts=strategy.samples if isinstance(strategy, ParallelBestOf) else 4,
        max_elapsed_seconds=3600,
        max_stage_dispatches=20,
        max_strategy_calls=8,
        max_no_progress=2,
    )
    defaults.update(limits)
    return MultiRolloutMethodPolicy(
        strategy, MultiRolloutLimits(**defaults), "isolated-workspace@1"
    )


def composition(p, overall="stages", reverse=False):
    methods = (
        MethodTemplate(get_profile("MultiRollout", "1.0"), p),
        MethodTemplate(
            get_profile("Swarm"),
            SwarmStagesPolicy("openapi-bot/v1", "trusted-evidence/v1", overall),
        ),
    )
    return MethodCompositionTemplate(
        tuple(reversed(methods)) if reverse else methods,
        relations=(
            MethodRelationTemplate("MultiRollout", "Swarm", CompositionRelation.NEST),
        ),
    )


class Isolation:
    def __init__(self, directory):
        self.directory, self.routes, self.revoked, self.unsafe, self.quota = (
            directory,
            {},
            False,
            False,
            2,
        )

    def require(self, reference, execution_ref, stages, context):
        if reference != "isolated-workspace@1" or self.unsafe:
            raise Invalid(
                "independent workspace does not authorize irreversible side effects"
            )
        if self.revoked:
            raise Forbidden("resource grant revoked")
        return self.quota

    async def prepare(self, reference, namespace, candidate, context):
        self.require(reference, "openapi-bot/v1", (), context)
        workspace = self.directory / namespace
        workspace.mkdir(parents=True, exist_ok=True)
        return dict(
            workspace=str(workspace),
            artifact_namespace=namespace,
            execution_key=namespace,
        )

    async def verify_route(self, reference, namespace, route, context):
        if self.routes.get(route.session_id) != namespace:
            raise Invalid("provider reused a mutable session across candidates")

    async def verify_artifacts(self, reference, namespace, artifacts, context):
        self.require(reference, "openapi-bot/v1", (), context)


class Evaluator:
    def __init__(self, directory):
        self.directory = directory
        self.calls, self.values = [], {}

    def require(self, context):
        return "trusted-file-evaluator@1"

    async def evaluate(self, descriptor, snapshot, qualified, context):
        self.calls.append((descriptor, qualified))
        # Inspect the actual isolated provider output, not a worker score claim.
        for rid in qualified:
            candidate = next(
                v for v in snapshot.multi.candidates if v.rollout_id == rid
            )
            output = json.loads(
                (self.directory / candidate.namespace / "result.txt").read_text()
            )
            if output["multi_rollout"]["candidate_id"] != rid:
                raise Invalid("evaluator inspected another candidate's file")
        return tuple(
            CandidateScore(
                rid, self.values.get(rid, 50), "verified file and acceptance evidence"
            )
            for rid in qualified
        )


class Strategies:
    def __init__(self, evaluator):
        self.selector = HighestScoreStrategy(
            evaluator, "frozen rubric: correctness", 0, 100
        )
        self.autonomous_grant = False
        self.propose_adjustment = AsyncMock()
        self.evaluate = AsyncMock(side_effect=self.selector.evaluate)

    def require(self, p, stages, context):
        if isinstance(p.strategy, ParallelBestOf):
            return self.selector.require(p, stages, context)
        if p.strategy.adaptation_ref != "stage-agent@1":
            raise Invalid("uninstalled adaptation reference")
        if p.strategy.feedback_mode == "autonomous" and not self.autonomous_grant:
            raise Forbidden("explicit autonomous delegation missing")
        return StrategyDescriptor(
            "stage-agent@1", "frozen acceptance", "trusted-file-evaluator@1", 0, 100
        )


@pytest.fixture
def multi(runtime, tmp_path):  # noqa: F811
    h = runtime
    h.isolation, h.evaluator = Isolation(tmp_path / "workspaces"), Evaluator(
        tmp_path / "workspaces"
    )
    h.strategy = Strategies(h.evaluator)
    h.notifications = SimpleNamespace(require=lambda: None, notify=AsyncMock())
    h.controller = MultiRolloutController(
        h.repository,
        h.strategy,
        h.isolation,
        h.notifications,
        h.authorization,
        h.interaction,
    )
    h.service = ProductProfileService(
        h.repository,
        ProfilePublisherAllowlist((h.owner.actor,)),
        h.authorization,
        h.execution,
        h.interaction,
        OpenApiStageSessionMessages(h.bot),
        h.controller,
    )
    h.engine = RolloutEngine(h.repository, h.execution, h.authorization, h.isolation)
    h.worker = RolloutIntentWorker(h.repository, h.engine, h.execution, h.controller)
    original = h.bot.send_message.side_effect

    async def send(**kwargs):
        value = json.loads(kwargs["message"])["multi_rollout"]
        workspace = Path(value["isolation"]["workspace"])
        (workspace / "result.txt").write_text(kwargs["message"])
        route = await original(**kwargs)
        h.isolation.routes[route.session_id] = value["namespace"]
        return route

    h.bot.send_message.side_effect = send

    async def create(p=None, stages=STAGE, overall="stages", reverse=False):
        h.profile = await h.service.create_product_profile(
            "multi", composition(p or policy(), overall, reverse), stages
        )
        h.collet = await h.service.create_product_collet(
            h.profile.id, "build an isolated result"
        )
        return h

    h.create = create
    return h
