import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import admission as admission_policy
from agentclaw.community.adapters.http.openapi_v1 import admission_managed_bot_routes as managed_admission
from agentclaw.community.adapters.http.openapi_v1.admission import (
    ADMISSION,
    ADMITTING_MODES,
    AdmissionMode,
)
from agentclaw.community.adapters.http.openapi_v1.authorization import (
    AUTHORIZATION,
    NoCheck,
    PublicAPIRoute,
)
_PATH = '/openapi/v1/bots/engine-admission-probe'
_OPERATION = ('GET', _PATH)


@pytest.fixture
def probe():
    async def handler():
        return {'ok': True}

    return handler


async def coding_targets():
    return [{'active_engine': 'aicoding'}]


def coding_bot_dependency():
    return coding_targets


@pytest.fixture
def probe_router(monkeypatch):
    monkeypatch.setitem(AUTHORIZATION, _OPERATION, NoCheck('engine admission probe'))
    return APIRouter(route_class=PublicAPIRoute)


@pytest.mark.parametrize('operation', ['restart-for-others', 'status-for-others'])
def test_managed_operations_declare_engine_admission(operation):
    mode = admission_policy.admission_for(('POST', '/openapi/v1/bots/' + operation))

    assert mode is AdmissionMode.ENGINE_CODING
    assert mode.engines == frozenset({'claude_code', 'aicoding'})


def test_managed_handlers_do_not_declare_engine_decorators():
    from agentclaw.community.adapters.http.openapi_v1.managed_bots.router import (
        restart_for_others,
        status_for_others,
    )

    assert not hasattr(restart_for_others, '__openapi_engine_check__')
    assert not hasattr(status_for_others, '__openapi_engine_check__')


def test_engine_mode_admits_application_callers():
    assert AdmissionMode.ENGINE_CODING in ADMITTING_MODES


def test_admission_inventory_contains_only_enum_members():
    assert all(isinstance(mode, AdmissionMode) for mode in ADMISSION.values())


def test_engine_allowlist_belongs_to_the_named_enum_member():
    mode = AdmissionMode.ENGINE_CODING

    assert mode.value == 'engine-coding'
    assert mode.engines == frozenset({'aicoding', 'claude_code'})
    assert isinstance(mode.engines, frozenset)
    assert not callable(mode)
    assert not hasattr(mode, 'bot_dependency')
    assert not hasattr(mode, 'mode')


@pytest.mark.parametrize('mode', list(AdmissionMode))
def test_admission_mode_round_trips_through_its_string_value(mode):
    assert AdmissionMode(mode.value) is mode
    assert str(mode) == mode.value


def test_engine_mode_requires_an_authorized_bot_dependency(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE_CODING)

    with pytest.raises(ValueError, match='authorized Bot dependency'):
        probe_router.add_api_route(_PATH, probe, methods=['GET'])


def test_engine_mode_requires_all_route_methods_to_agree(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE_CODING)
    monkeypatch.setitem(ADMISSION, ('POST', _PATH), AdmissionMode.OPEN)
    monkeypatch.setitem(AUTHORIZATION, ('POST', _PATH), AUTHORIZATION[_OPERATION])
    with pytest.raises(ValueError, match='identical admission modes'):
        probe_router.add_api_route(_PATH, probe, methods=['GET', 'POST'])


def test_engine_mode_requires_a_bot_dependency_for_every_route_method(monkeypatch, probe_router, probe):
    for method in ['GET', 'POST']:
        monkeypatch.setitem(ADMISSION, (method, _PATH), AdmissionMode.ENGINE_CODING)
        monkeypatch.setitem(AUTHORIZATION, (method, _PATH), AUTHORIZATION[_OPERATION])
    monkeypatch.setitem(managed_admission.ENGINE_BOT_DEPENDENCIES, _OPERATION, coding_bot_dependency)

    with pytest.raises(ValueError, match='authorized Bot dependency'):
        probe_router.add_api_route(_PATH, probe, methods=['GET', 'POST'])


def test_engine_mode_requires_all_route_methods_to_resolve_the_same_bots(monkeypatch, probe_router, probe):
    for method, provider in [('GET', coding_bot_dependency), ('POST', lambda: coding_targets)]:
        monkeypatch.setitem(ADMISSION, (method, _PATH), AdmissionMode.ENGINE_CODING)
        monkeypatch.setitem(managed_admission.ENGINE_BOT_DEPENDENCIES, (method, _PATH), provider)
        monkeypatch.setitem(AUTHORIZATION, (method, _PATH), AUTHORIZATION[_OPERATION])

    with pytest.raises(ValueError, match='identical Bot dependencies'):
        probe_router.add_api_route(_PATH, probe, methods=['GET', 'POST'])


def test_engine_mode_accepts_matching_methods_and_ignores_head_options(monkeypatch, probe_router, probe):
    for method in ['GET', 'POST']:
        monkeypatch.setitem(ADMISSION, (method, _PATH), AdmissionMode.ENGINE_CODING)
        monkeypatch.setitem(managed_admission.ENGINE_BOT_DEPENDENCIES, (method, _PATH), coding_bot_dependency)
        monkeypatch.setitem(AUTHORIZATION, (method, _PATH), AUTHORIZATION[_OPERATION])

    route_options = {'methods': {'GET', 'POST', 'HEAD', 'OPTIONS'}}
    admission_policy.apply_admission(_PATH, route_options)

    assert len(route_options['dependencies']) == 1
    assert 501 in route_options['responses']


@pytest.mark.parametrize('engine', ['claude_code', 'aicoding', 'openclaw'])
def test_engine_mode_enforces_its_configured_engines(monkeypatch, probe_router, probe, engine):
    bots = [{'active_engine': engine}]

    async def targets():
        return bots

    monkeypatch.setattr(AdmissionMode.ENGINE_CODING, 'engines', frozenset({engine}))
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE_CODING)
    monkeypatch.setitem(managed_admission.ENGINE_BOT_DEPENDENCIES, _OPERATION, lambda: targets)
    probe_router.add_api_route(_PATH, probe, methods=['GET'])
    app = FastAPI()
    app.include_router(probe_router)
    client = TestClient(app)

    assert app.openapi()['paths'][_PATH]['get']['responses']['501']['description'] == (
        f'Supported engines: {engine}.'
    )
    assert client.get(_PATH).status_code == 200

    bots[0]['active_engine'] = 'other-engine'
    response = client.get(_PATH)

    assert response.status_code == 501
    assert response.json()['code'] == 501000
    assert response.json()['message'] == 'Not supported for this engine'


@pytest.mark.parametrize('mode', list(AdmissionMode))
def test_admission_lookup_preserves_existing_modes(monkeypatch, mode):
    monkeypatch.setitem(ADMISSION, _OPERATION, mode)

    assert admission_policy.admission_for(_OPERATION) is mode


def test_unknown_operation_is_refused(monkeypatch):
    monkeypatch.delitem(ADMISSION, _OPERATION, raising=False)

    assert admission_policy.admission_for(_OPERATION) is AdmissionMode.REFUSED


@pytest.mark.parametrize('mode', [mode for mode in AdmissionMode if mode is not AdmissionMode.ENGINE_CODING])
def test_other_modes_do_not_acquire_an_engine_gate(monkeypatch, probe_router, probe, mode):
    assert mode.engines == frozenset()
    monkeypatch.setitem(ADMISSION, _OPERATION, mode)
    probe_router.add_api_route(_PATH, probe, methods=['GET'])

    assert 501 not in probe_router.routes[0].responses
