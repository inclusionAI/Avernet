import json
import time
from typing import Any

import jwt

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.repository.models import TemplateModel
from agentclaw.community.di.openapi_bot_operations_config import OpenApiBotOperationsConfig
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.utils.avernet_tenant import DEFAULT_AVERNET_TENANT
from agentclaw.community.utils.env_utils import get_current_env
from agentclaw.community.utils.gateway_principal_config import init_principal_verifier_config
from tests.community.factories.bot_collaborator import make_bot
from tests.community.framework import CaseInput, ExpectError, ExpectSuccess, endpoint_test
from tests.community.framework.di_seams import bind_method
from tests.community.framework.world import World

_KEY = 'managed-bot-endpoint-test-signing-key'
_APP = 'managed-bot-endpoint-test-app'
_ADDRESS = {'bot_id': 'managed-bot', 'owner_id': 'owner', 'template_key': 'coding'}


class _Secret:
    secret_user = 'test'
    secret_value = _KEY


class _Resolver:
    def get_secret(self, _secret_name: str) -> _Secret:
        return _Secret()


def _headers() -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode({
        'iss': 'gateway', 'aud': 'backend', 'iat': now, 'exp': now + 3600,
        'principals': [{
            'type': 'app', 'tenant': DEFAULT_AVERNET_TENANT,
            'app': {'app_id': 77, 'app_name': _APP, 'owners': 'test-team', 'tenant': DEFAULT_AVERNET_TENANT},
        }],
    }, _KEY, algorithm='HS256')
    return {PRINCIPAL_HEADER: token}


async def _accept_restart(
    _self: BotServiceProtocol,
    bot_id: str,
    user_id: str,
    extra_configs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    assert (bot_id, user_id) == ('managed-bot', 'owner')
    return {'bot_id': bot_id, 'owner_id': user_id, 'status': 'PENDING', 'active_engine': 'aicoding'}


def _seed(world: World) -> None:
    init_principal_verifier_config(_Resolver(), 'managed-bot-endpoint-test-key', strict=False)
    world.injector.binder.bind(OpenApiBotOperationsConfig, to=OpenApiBotOperationsConfig(
        frozenset({(get_current_env(), DEFAULT_AVERNET_TENANT, _APP)}),
    ))
    make_bot(world, bot_id='managed-bot', owner_id='owner', active_engine='aicoding', bot_type='personal')
    with world.get(DatabasePlugin).orm_session() as session:
        session.add(TemplateModel(bot_id='managed-bot', ext=json.dumps({
            'template_key': 'coding', 'template_version_id': 3,
        })))
    bind_method(world, BotServiceProtocol, 'restart_bot_async', _accept_restart)


@endpoint_test(
    method='POST', path='/openapi/v1/bots/template-queries', scenario='managed_query_happy',
    seed=_seed, input=CaseInput(headers=_headers(), json_body={'template_key': 'coding'}),
    expect=ExpectSuccess(json_contains={'code': 200000, 'data': {'total': 1, 'items': [{
        'bot_id': 'managed-bot', 'owner_id': 'owner', 'template_version_id': 3,
    }]}}),
)
def managed_query_happy() -> None:
    pass


@endpoint_test(
    method='POST', path='/openapi/v1/bots/template-queries', scenario='managed_query_invalid_limit',
    seed=_seed, input=CaseInput(headers=_headers(), json_body={'template_key': 'coding', 'limit': 101}),
    expect=ExpectError(status=422),
)
def managed_query_invalid_limit() -> None:
    pass


@endpoint_test(
    method='POST', path='/openapi/v1/bots/template-config-for-others', scenario='managed_snapshot_happy',
    seed=_seed, input=CaseInput(headers=_headers(), json_body=_ADDRESS),
    expect=ExpectSuccess(json_contains={'code': 200000, 'data': {'template_config': {
        'template_key': 'coding', 'template_version_id': 3,
    }}}),
)
def managed_snapshot_happy() -> None:
    pass


@endpoint_test(
    method='POST', path='/openapi/v1/bots/template-config-for-others', scenario='managed_snapshot_wrong_scope',
    seed=_seed, input=CaseInput(headers=_headers(), json_body={**_ADDRESS, 'template_key': 'other'}),
    expect=ExpectError(status=404),
)
def managed_snapshot_wrong_scope() -> None:
    pass


@endpoint_test(
    method='POST', path='/openapi/v1/bots/restart-for-others', scenario='managed_restart_happy',
    seed=_seed, input=CaseInput(headers=_headers(), json_body=_ADDRESS),
    expect=ExpectSuccess(json_contains={'code': 200000, 'data': {'bot_id': 'managed-bot', 'status': 'PENDING'}}),
)
def managed_restart_happy() -> None:
    pass


@endpoint_test(
    method='POST', path='/openapi/v1/bots/restart-for-others', scenario='managed_restart_wrong_scope',
    seed=_seed, input=CaseInput(headers=_headers(), json_body={**_ADDRESS, 'owner_id': 'other'}),
    expect=ExpectError(status=404),
)
def managed_restart_wrong_scope() -> None:
    pass


@endpoint_test(
    method='POST', path='/openapi/v1/bots/status-for-others', scenario='managed_status_happy',
    seed=_seed, input=CaseInput(headers=_headers(), json_body={
        'template_key': 'coding', 'bots': [{'bot_id': 'managed-bot', 'owner_id': 'owner'}],
    }),
    expect=ExpectSuccess(json_contains={'code': 200000, 'data': {'total': 1, 'items': [{
        'bot_id': 'managed-bot', 'owner_id': 'owner', 'status': 'ACTIVE',
    }]}}),
)
def managed_status_happy() -> None:
    pass


@endpoint_test(
    method='POST', path='/openapi/v1/bots/status-for-others', scenario='managed_status_empty_batch',
    seed=_seed, input=CaseInput(headers=_headers(), json_body={'template_key': 'coding', 'bots': []}),
    expect=ExpectError(status=422),
)
def managed_status_empty_batch() -> None:
    pass
