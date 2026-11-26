"""SQL execution-fact fixtures with an ephemeral signed Bot principal."""

import secrets
from dataclasses import replace
from time import time
from types import SimpleNamespace

import jwt

from agentclaw.community.api.task.rollout_progress_service import (
    RolloutProgressServiceProtocol,
)
from agentclaw.community.core.task.context_collab.g1_secure_govern.product_runtime import (
    OwnerProductRuntimeAuthorization,
)
from agentclaw.community.core.task.context_collab.l4_context_primitives.product_status import (
    StatusEnum,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_contracts import (
    SessionRoute,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_engine import (
    RolloutEngine,
)
from agentclaw.community.core.task.context_collab.l5_methods.rollout_lifecycle import (
    replace_stage,
)
from agentclaw.community.core.task.context_collab.l6_products.persistence.rollout_repository import (
    SqlProductRuntimeRepository,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from tests.community.endpoints.product_profile_seeds import (
    seed_collet,
    IsolatedExecutionBoundary,
)

_KEY = secrets.token_hex(32)


class SigningKeyResolver:
    def get_secret(self, name):
        return SimpleNamespace(secret_value=_KEY)


def signed_headers():
    now = int(time())
    tenant = DEFAULT_AVERNET_TENANT
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 3600,
            "principals": [
                {
                    "type": "bot",
                    "tenant": tenant,
                    "bot": {
                        "bot_uuid": "worker",
                        "owner_id": "u1",
                        "tenant": tenant,
                        "app_id": 1,
                        "agent_code": "executor",
                    },
                }
            ],
        },
        _KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def seed_running_stage(world):
    seed_collet(world)
    init_principal_verifier_config(
        SigningKeyResolver(), "ephemeral-test-key", strict=False
    )
    repository = SqlProductRuntimeRepository(world.get(DatabasePlugin))
    snapshot = repository.get_collet(1)
    stage = snapshot.current().stages[0]
    # Controlled test fixture for an observed external run; no production
    # executor or fake in-domain completion is installed by this seed.
    replace_stage(
        snapshot,
        replace(
            stage,
            status=StatusEnum.RUNNING,
            assignee_type="bot",
            assignee_uuid="worker",
            session_id="fixture-session",
        ),
    )
    record = snapshot.records[0]
    record.attempt, record.lease_until, record.dispatch_state = (
        "fixture-attempt",
        int(time()) + 900,
        "delivered",
    )
    record.route = SessionRoute(
        "openapi", "fixture-session", "worker", "u1", "production", "fixture-run"
    )
    repository.save(snapshot, 1)
    world.injector.binder.bind(
        RolloutProgressServiceProtocol,
        to=RolloutEngine(
            repository, IsolatedExecutionBoundary(), OwnerProductRuntimeAuthorization()
        ),
    )


REPORT = {
    "request_id": "endpoint-report",
    "attempt": "fixture-attempt",
    "expected_revision": 2,
    "event": {
        "type": "RUN_FAILED",
        "stage_rollout_id": 3,
        "artifacts": [],
        "acceptances": [],
        "error_msg": "fixture runtime failed",
    },
}
