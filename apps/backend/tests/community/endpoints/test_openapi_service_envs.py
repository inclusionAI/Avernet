"""Environment HTTP contracts through the production app and per-test DI.

Only long-running publish orchestration is substituted; resource lookup and
validation use real repositories and services.
"""
import time

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.publish_flow_service import PublishFlowServiceProtocol
from agentclaw.community.core.repository.protocols.publishing import BotPublishRepositoryProtocol
from agentclaw.community.core.service_bot.repository.models import PublishStatus
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, bind_overrides, endpoint_test

_KEY = "service-env-test-signing-key-at-least-32-bytes"
_OWNER = "env-owner"
_BOT = "env-source"
_PUBLISH = "/openapi/v1/bots/{bot_id}/service-envs/{publication_id}/env-publish"
_PROGRESS = "/openapi/v1/service-envs/env-publish/{baas_publish_id}/progress"
_TEARDOWN = "/openapi/v1/service-envs/{bot_uuid}/env-teardown"


class _Secret:
    secret_user = "test"
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _name):
        return _Secret()


def _headers():
    now = int(time.time())
    return {PRINCIPAL_HEADER: jwt.encode({
        "iss": "gateway", "aud": "backend", "iat": now, "exp": now + 3600,
        "principals": [{"type": "user", "subject": {"id": _OWNER, "username": "env@example.test"}}],
    }, _KEY, algorithm="HS256")}


def _auth(world):
    init_principal_verifier_config(_Resolver(), "gateway_principal_signing_key", strict=False)


async def _publish(self, *, publish_id, operator, biz_id, resource_spec=None):
    assert resource_spec is None
    assert (publish_id, operator, biz_id) == (1, "operator-1", "task-1")
    return {"success": True, "publish_id": 1, "stage": "eval", "bot_uuid": "eval-1", "baas_publish_id": 42}


def _progress(self, *, baas_publish_id, include_devices):
    assert baas_publish_id == 42 and include_devices is False
    return {"publish_id": 42, "status": "SUCCESS"}


def _teardown(self, bot_uuid, *, operator, publish_id):
    assert (bot_uuid, operator, publish_id) == ("eval-1", "operator-1", 1)
    return {"success": True, "bot_uuid": bot_uuid, "default_tag": "eval", "message": "enqueued"}


def _seed(world):
    _auth(world)
    bot = make_bot(world, bot_id=_BOT, owner_id=_OWNER)
    record = world.get(BotPublishRepositoryProtocol).insert({
        "source_bot_pk": bot["id"], "source_bot_id": _BOT, "publish_bot_id": _BOT,
        "name": "Environment source", "owner_id": _OWNER, "permission_owner": _OWNER,
        "status": PublishStatus.SUCCESS, "version": 1, "env": get_current_env(),
    })
    assert record.id == 1
    bind_overrides(world, PublishFlowServiceProtocol, {
        "eval_publish": _publish, "get_baas_publish_progress": _progress, "eval_teardown": _teardown,
    })


@endpoint_test(method="POST", path=_PUBLISH, scenario="env_publish_ok", seed=_seed,
    input=CaseInput(path_params={"bot_id": _BOT, "publication_id": 1},
        query_params={"owner_id": _OWNER, "operator": "operator-1", "biz_id": "task-1"}, headers=_headers()),
    expect=ExpectSuccess(status=202, json_contains={"data": {"success": True, "baas_publish_id": 42}}))
def publish_ok():
    pass


@endpoint_test(method="POST", path=_PUBLISH, scenario="env_publication_missing", seed=_auth,
    input=CaseInput(path_params={"bot_id": _BOT, "publication_id": 999},
        query_params={"owner_id": _OWNER, "operator": "operator-1"}, headers=_headers()),
    expect=ExpectError(status=404))
def publish_missing():
    pass


@endpoint_test(method="GET", path=_PROGRESS, scenario="env_progress_ok", seed=_seed,
    input=CaseInput(path_params={"baas_publish_id": 42}, headers=_headers()),
    expect=ExpectSuccess(status=200, json_contains={"data": {"publish_id": 42, "status": "SUCCESS"}}))
def progress_ok():
    pass


@endpoint_test(method="GET", path=_PROGRESS, scenario="env_invalid_workflow", seed=_auth,
    input=CaseInput(path_params={"baas_publish_id": 0}, headers=_headers()), expect=ExpectError(status=422))
def progress_invalid():
    pass


@endpoint_test(method="POST", path=_TEARDOWN, scenario="env_teardown_ok", seed=_seed,
    input=CaseInput(path_params={"bot_uuid": "eval-1"},
        query_params={"operator": "operator-1", "publish_id": 1}, headers=_headers()),
    expect=ExpectSuccess(status=202, json_contains={"data": {"success": True, "bot_uuid": "eval-1"}}))
def teardown_ok():
    pass


@endpoint_test(method="POST", path=_TEARDOWN, scenario="env_invalid_publication", seed=_auth,
    input=CaseInput(path_params={"bot_uuid": "eval-1"}, query_params={"publish_id": -1}, headers=_headers()),
    expect=ExpectError(status=422))
def teardown_invalid():
    pass


# OPEN admits caller shapes; it does not accept requests without a verified caller.
for _method, _path, _params in (
    ("POST", _PUBLISH, {"bot_id": _BOT, "publication_id": 1}),
    ("GET", _PROGRESS, {"baas_publish_id": 42}),
    ("POST", _TEARDOWN, {"bot_uuid": "eval-1"}),
):
    endpoint_test(method=_method, path=_path, scenario="env_missing_principal", seed=_auth,
        input=CaseInput(path_params=_params, query_params={"owner_id": _OWNER, "operator": "operator-1"}),
        expect=ExpectError(status=401))(lambda: None)


async def _publish_resources(self, *, publish_id, operator, biz_id, resource_spec):
    assert resource_spec.to_dict() == {"cpu": 4, "memory": 8, "disk": 200}
    return {"success": True, "publish_id": publish_id, "stage": "eval", "bot_uuid": "eval-1", "baas_publish_id": 42}


def _seed_resources(world):
    _seed(world)
    bind_overrides(world, PublishFlowServiceProtocol, {"eval_publish": _publish_resources})


@endpoint_test(method="POST", path=_PUBLISH, scenario="env_publish_resources", seed=_seed_resources,
    input=CaseInput(path_params={"bot_id": _BOT, "publication_id": 1},
        query_params={"owner_id": _OWNER, "operator": "operator-1", "cpu": 4, "memory": 8, "disk": 200}, headers=_headers()),
    expect=ExpectSuccess(status=202, json_contains={"data": {"success": True}}))
def publish_resources():
    pass


for _case, _resources in [
    ("cpu_only", {"cpu": 4}), ("memory_only", {"memory": 8}),
    ("disk_only", {"disk": 200}),
    ("cpu_disk", {"cpu": 4, "disk": 200}),
    ("memory_disk", {"memory": 8, "disk": 200}),
    ("zero_cpu", {"cpu": 0, "memory": 8}),
    ("negative_memory", {"cpu": 4, "memory": -1}),
    ("zero_disk", {"cpu": 4, "memory": 8, "disk": 0}),
    ("fractional_disk", {"cpu": 4, "memory": 8, "disk": "1.5"}),
]:
    endpoint_test(method="POST", path=_PUBLISH, scenario=f"env_publish_resources_{_case}", seed=_auth,
        input=CaseInput(path_params={"bot_id": _BOT, "publication_id": 1},
            query_params={"owner_id": _OWNER, "operator": "operator-1", **_resources}, headers=_headers()),
        expect=ExpectError(status=422))(lambda: None)
