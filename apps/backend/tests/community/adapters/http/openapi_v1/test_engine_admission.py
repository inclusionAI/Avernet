import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

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
from agentclaw.community.adapters.http.openapi_v1.engine_access import EngineCheck

_PATH = '/openapi/v1/bots/engine-admission-probe'
_OPERATION = ('GET', _PATH)


@pytest.fixture
def probe():
    async def handler():
        return {'ok': True}

    return handler


async def coding_targets():
    return [{'active_engine': 'aicoding'}]


@pytest.fixture
def probe_router(monkeypatch):
    monkeypatch.setitem(AUTHORIZATION, _OPERATION, NoCheck('engine admission probe'))
    return APIRouter(route_class=PublicAPIRoute)


@pytest.mark.parametrize('operation', ['restart-for-others', 'status-for-others'])
def test_managed_operations_declare_engine_admission(operation):
    assert ADMISSION[('POST', '/openapi/v1/bots/' + operation)] is AdmissionMode.ENGINE


def test_engine_mode_admits_application_callers():
    assert AdmissionMode.ENGINE in ADMITTING_MODES


def test_engine_mode_requires_configuration(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE)

    with pytest.raises(ValueError, match='EngineCheck'):
        probe_router.add_api_route(_PATH, probe, methods=['GET'])


@pytest.mark.parametrize('mode', [AdmissionMode.OPEN, AdmissionMode.REFUSED])
def test_engine_configuration_requires_engine_mode(monkeypatch, probe_router, probe, mode):
    monkeypatch.setitem(ADMISSION, _OPERATION, mode)
    handler = EngineCheck(engines=frozenset({'aicoding'}), bots=coding_targets)(probe)

    with pytest.raises(ValueError, match='AdmissionMode.ENGINE'):
        probe_router.add_api_route(_PATH, handler, methods=['GET'])


def test_engine_mode_requires_a_nonempty_allowlist(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE)
    handler = EngineCheck(engines=frozenset(), bots=coding_targets)(probe)

    with pytest.raises(ValueError, match='non-empty'):
        probe_router.add_api_route(_PATH, handler, methods=['GET'])


def test_engine_mode_requires_all_route_methods_to_agree(monkeypatch, probe_router, probe):
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE)
    monkeypatch.setitem(ADMISSION, ('POST', _PATH), AdmissionMode.OPEN)
    monkeypatch.setitem(AUTHORIZATION, ('POST', _PATH), AUTHORIZATION[_OPERATION])
    handler = EngineCheck(engines=frozenset({'aicoding'}), bots=coding_targets)(probe)

    with pytest.raises(ValueError, match='AdmissionMode.ENGINE'):
        probe_router.add_api_route(_PATH, handler, methods=['GET', 'POST'])


@pytest.mark.parametrize('engine', ['claude_code', 'aicoding', 'openclaw'])
def test_engine_mode_enforces_its_configured_engines(monkeypatch, probe_router, probe, engine):
    monkeypatch.setitem(ADMISSION, _OPERATION, AdmissionMode.ENGINE)
    bots = [{'active_engine': engine}]

    async def targets():
        return bots

    handler = EngineCheck(engines=frozenset({engine}), bots=targets)(probe)
    probe_router.add_api_route(_PATH, handler, methods=['GET'])
    app = FastAPI()
    app.include_router(probe_router)
    client = TestClient(app)

    assert client.get(_PATH).status_code == 200

    bots[0]['active_engine'] = 'other-engine'
    response = client.get(_PATH)

    assert response.status_code == 501
    assert response.json()['code'] == 501000
