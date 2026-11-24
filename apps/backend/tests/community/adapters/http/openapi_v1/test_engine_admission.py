import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1 import admission as admission_policy
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
    rule = admission_policy.admission_for(('POST', '/openapi/v1/bots/' + operation))

    assert rule.mode is AdmissionMode.ENGINE
    assert rule.engines == frozenset({'claude_code', 'aicoding'})
    assert callable(rule.bot_dependency())


def test_managed_handlers_do_not_declare_engine_decorators():
    from agentclaw.community.adapters.http.openapi_v1.managed_bots.router import (
        restart_for_others,
        status_for_others,
    )

    assert not hasattr(restart_for_others, '__openapi_engine_check__')
    assert not hasattr(status_for_others, '__openapi_engine_check__')


def test_engine_mode_admits_application_callers():
    assert AdmissionMode.ENGINE in ADMITTING_MODES


def test_engine_mode_requires_configuration(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE)

    with pytest.raises(ValueError, match='non-empty engine allowlist'):
        probe_router.add_api_route(_PATH, probe, methods=['GET'])


@pytest.mark.parametrize('mode', [AdmissionMode.OPEN, AdmissionMode.REFUSED])
def test_engine_configuration_requires_engine_mode(monkeypatch, probe_router, probe, mode):
    monkeypatch.setitem(ADMISSION, _OPERATION, admission_policy.AdmissionRule(
        mode=mode, engines=frozenset({'aicoding'}), bot_dependency=coding_bot_dependency,
    ))

    with pytest.raises(ValueError, match='AdmissionMode.ENGINE'):
        probe_router.add_api_route(_PATH, probe, methods=['GET'])


def test_engine_mode_requires_a_nonempty_allowlist(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, admission_policy.AdmissionRule(
        mode=AdmissionMode.ENGINE, bot_dependency=coding_bot_dependency,
    ))

    with pytest.raises(ValueError, match='non-empty'):
        probe_router.add_api_route(_PATH, probe, methods=['GET'])


def test_engine_mode_requires_a_bot_dependency(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, admission_policy.AdmissionRule(
        mode=AdmissionMode.ENGINE, engines=frozenset({'aicoding'}),
    ))

    with pytest.raises(ValueError, match='Bot dependency'):
        probe_router.add_api_route(_PATH, probe, methods=['GET'])


def test_engine_mode_requires_all_route_methods_to_agree(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, admission_policy.AdmissionRule(
        mode=AdmissionMode.ENGINE, engines=frozenset({'aicoding'}),
        bot_dependency=coding_bot_dependency,
    ))
    monkeypatch.setitem(ADMISSION, ('POST', _PATH), AdmissionMode.OPEN)
    monkeypatch.setitem(AUTHORIZATION, ('POST', _PATH), AUTHORIZATION[_OPERATION])
    with pytest.raises(ValueError, match='identical admission rules'):
        probe_router.add_api_route(_PATH, probe, methods=['GET', 'POST'])


def test_engine_mode_requires_all_route_methods_to_have_the_same_parameters(
    monkeypatch, probe_router, probe,
):
    for method, engine in [('GET', 'aicoding'), ('POST', 'claude_code')]:
        monkeypatch.setitem(ADMISSION, (method, _PATH), admission_policy.AdmissionRule(
            mode=AdmissionMode.ENGINE, engines=frozenset({engine}),
            bot_dependency=coding_bot_dependency,
        ))
        monkeypatch.setitem(AUTHORIZATION, (method, _PATH), AUTHORIZATION[_OPERATION])

    with pytest.raises(ValueError, match='identical admission rules'):
        probe_router.add_api_route(_PATH, probe, methods=['GET', 'POST'])


@pytest.mark.parametrize('engine', ['claude_code', 'aicoding', 'openclaw'])
def test_engine_mode_enforces_its_configured_engines(monkeypatch, probe_router, probe, engine):
    bots = [{'active_engine': engine}]

    async def targets():
        return bots

    monkeypatch.setitem(ADMISSION, _OPERATION, admission_policy.AdmissionRule(
        mode=AdmissionMode.ENGINE, engines=frozenset({engine}), bot_dependency=lambda: targets,
    ))
    probe_router.add_api_route(_PATH, probe, methods=['GET'])
    app = FastAPI()
    app.include_router(probe_router)
    client = TestClient(app)

    assert client.get(_PATH).status_code == 200

    bots[0]['active_engine'] = 'other-engine'
    response = client.get(_PATH)

    assert response.status_code == 501
    assert response.json()['code'] == 501000
    assert response.json()['message'] == 'Not supported for this engine'


@pytest.mark.parametrize('mode', list(AdmissionMode))
def test_admission_lookup_preserves_existing_modes(monkeypatch, mode):
    monkeypatch.setitem(ADMISSION, _OPERATION, mode)

    assert admission_policy.admission_for(_OPERATION).mode is mode


def test_unknown_operation_is_refused(monkeypatch):
    monkeypatch.delitem(ADMISSION, _OPERATION, raising=False)

    assert admission_policy.admission_for(_OPERATION).mode is AdmissionMode.REFUSED


@pytest.mark.parametrize('mode', [mode for mode in AdmissionMode if mode is not AdmissionMode.ENGINE])
def test_other_modes_do_not_acquire_an_engine_gate(monkeypatch, probe_router, probe, mode):
    monkeypatch.setitem(ADMISSION, _OPERATION, mode)
    probe_router.add_api_route(_PATH, probe, methods=['GET'])

    assert 501 not in probe_router.routes[0].responses
