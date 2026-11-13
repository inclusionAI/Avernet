"""Framework cases use real DI, product publication, signed Bot auth and SQL."""

from datetime import datetime, timedelta, timezone
import secrets

import jwt

from agentclaw.community.api.task.task_commission_service import TaskCommissionServiceProtocol
from agentclaw.community.core.task.context_collab.g1_secure_govern.actor_context import ActorContext
from agentclaw.community.core.task.context_collab.l2_agent_network.network_refs import PrincipalRef
from agentclaw.community.core.task.context_collab.l3_spaces_resources.space_resource_refs import SpaceRef
from agentclaw.community.core.task.context_collab.products.task_commission.service_api import PublishTaskCommand
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.task_commission import BOT_ID, OWNER, commission_report_request
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

_HEADERS: dict[str, str] = {}
_BODY: dict = {}


def _seed_report(world) -> None:
    key = secrets.token_hex(32)

    class Secret:
        secret_user = "test"
        secret_value = key

    class Resolver:
        def get_secret(self, _name):
            return Secret()

    init_principal_verifier_config(Resolver(), "test-key", strict=False)
    service = world.get(TaskCommissionServiceProtocol)
    created = service.publish_task(
        PublishTaskCommand(
            title="委托报告", body="整理调查结论", space=SpaceRef("space_1", "test"),
            assignee_agent_id=BOT_ID,
        ),
        ActorContext(PrincipalRef("human", "u_publisher"), source="api"),
    )
    now = datetime.now(timezone.utc)
    signed = jwt.encode({
        "iss": "gateway", "aud": "backend", "iat": now,
        "exp": now + timedelta(minutes=1),
        "principals": [{"type": "bot", "tenant": DEFAULT_AVERNET_TENANT, "bot": {
            "bot_uuid": BOT_ID, "owner_id": OWNER, "app_id": 1,
            "agent_code": "writer", "tenant": DEFAULT_AVERNET_TENANT,
        }}],
    }, key, algorithm="HS256")
    _HEADERS.clear()
    _HEADERS["X-Avernet-Principal"] = signed
    _BODY.clear()
    _BODY.update(commission_report_request(created, revision=created.context_version))


def _assert_started(response, world) -> None:
    result = response.json()["result"]["parts"][0]["data"]
    ref = result["result_refs"][0]
    assert ref["version"] == "3"
    detail = world.get(TaskCommissionServiceProtocol).detail_task(ref["object_id"])
    assert detail.status == "in_progress"
    assert detail.events[-1]["actor_id"] == BOT_ID


@endpoint_test(
    method="POST", path="/a2a/task-commission", scenario="designated_bot_starts_task",
    seed=_seed_report, input=CaseInput(headers=_HEADERS, json_body=_BODY),
    expect=ExpectSuccess(status=200, json_contains={"result": {"parts": [
        {"kind": "data", "data": {"status": "SUCCEEDED"}},
    ]}}),
    extra_assertions=(_assert_started,),
)
def designated_bot_starts_task():
    """The actual endpoint authenticates the Bot and persists its start event."""


@endpoint_test(
    method="POST", path="/a2a/task-commission", scenario="unauthenticated",
    input=CaseInput(json_body={}),
    expect=ExpectError(status=401, json_contains={"detail": "Unauthorized"}),
)
def unauthenticated_report():
    """No signature or SSO fallback is accepted."""
