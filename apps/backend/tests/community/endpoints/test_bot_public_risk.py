"""Risk HTTP contracts through the assembled app and test-profile Injector."""
from datetime import datetime, timedelta
import json
import time

from injector import InstanceProvider
import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_public_risk.application import RiskInputBuilder
from agentclaw.community.core.bot_public_risk.repository import BotPublicRiskRepository, RiskCheck
from agentclaw.community.core.bot_public_risk.tasks import SecurityProvider, SecurityReport
from agentclaw.community.core.task_queue.services.task_queue_service import TaskQueueService
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.access import make_staff_user
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test

OWNER = "risk-owner"
KEY = "risk-framework-signing-test-key-at-least-32-bytes"


class Resolver:
    def get_secret(self, _):
        return type("Secret", (), {"secret_user": "test", "secret_value": KEY})()


class Builder:
    def build(self, *, bot, target):
        return {"agent_info": {"agentCode": "test-agent"}}, "a" * 64


class Security:
    def submit_agent_scan(self, agent_info):
        return "security-check"
    def get_agent_check_report(self, check_id):
        assert check_id == "security-check"
        return SecurityReport(status="REJECT", detail_url="https://example.com/report")


class Queue:
    def enqueue(self, *args, **kwargs):
        return None


def seed(world):
    init_principal_verifier_config(Resolver(), "risk-test", strict=False)
    make_staff_user(world, user_id=OWNER)
    bot = make_bot(world, bot_id="riskbot", owner_id=OWNER, bot_type="personal")
    world.injector.binder.bind(RiskInputBuilder, to=InstanceProvider(Builder()))
    world.injector.binder.bind(SecurityProvider, to=InstanceProvider(Security()))
    world.injector.binder.bind(TaskQueueService, to=InstanceProvider(Queue()))
    repository = world.get(BotPublicRiskRepository)
    for scope in ("legacy", "user"):
        target = {"public_scope": scope, "visibility": "PUBLIC" if scope == "legacy" else "public", "view_depts": []}
        if scope == "legacy":
            target.update(permission_owner="caller", friend_approval="0")
        repository.create_or_replay(RiskCheck(
            risk_check_id=f"risk-{scope}", env=get_current_env(), bot_pk=bot["id"], owner_id=OWNER,
            operator_id=OWNER, client_scope=f"{scope}:{OWNER}", client_request_id=f"seed-{scope}",
            public_scope=scope, target_json=json.dumps(target), request_hash="r", input_json="{}", input_hash="h",
            approval_biz_id=f"biz-{scope}", phase="CHECKING", security_status="DETECTING", check_id="security-check",
            auto_poll_until=datetime.now() + timedelta(minutes=5),
        ))


def headers():
    now = int(time.time())
    token = jwt.encode({"iss": "gateway", "aud": "backend", "iat": now, "exp": now + 3600,
                        "principals": [{"type": "user", "subject": {"id": OWNER, "username": OWNER}, "resolved_by": {"method": "buc"}}]}, KEY, algorithm="HS256")
    return {PRINCIPAL_HEADER: token, "x-user-id": OWNER}


for surface in ("legacy", "openapi"):
    base = ("/api/bots/{bot_id}" if surface == "legacy" else "/openapi/v1/collaboration/bots/{bot_id}") + "/public/risk-checks"
    valid_bot = "riskbot" if surface == "legacy" else f"riskbot:{OWNER}"
    bad_bot = "absent" if surface == "legacy" else f"absent:{OWNER}"
    scope = "legacy" if surface == "legacy" else "user"
    body = {"client_request_id": "new-request", "public": "1"} if surface == "legacy" else {"client_request_id": "new-request", "public_scope": "user", "visibility": "public"}
    query = {} if surface == "legacy" else {"user_id": OWNER}
    for method, suffix, result in (("POST", "", "create"), ("GET", "/current", "current"), ("GET", "/{risk_check_id}", "get"), ("POST", "/{risk_check_id}/refresh", "refresh")):
        params = {"bot_id": valid_bot, "risk_check_id": f"risk-{scope}"}
        request_query = {**query, **({"public_scope": "user"} if result == "current" and surface == "openapi" else {})}
        data = body if result == "create" else None
        expected = {"success": True} if surface == "legacy" else {"code": 202000 if result == "create" else 200000}
        if result == "refresh":
            expected["data"] = {"outcome": "UPDATED"}
        endpoint_test(method=method, path=base + suffix, scenario="happy", seed=seed,
                      input=CaseInput(path_params=params, query_params=request_query, headers=headers(), json_body=data),
                      expect=ExpectSuccess(status=202 if surface == "openapi" and result == "create" else 200, json_contains=expected))(lambda: None)
        endpoint_test(method=method, path=base + suffix, scenario="missing_bot", seed=seed,
                      input=CaseInput(path_params={**params, "bot_id": bad_bot}, query_params=request_query, headers=headers(), json_body=data),
                      expect=ExpectError(status=404))(lambda: None)
