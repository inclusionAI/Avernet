"""Simulator AgentPass routes over the wire (D8).

A minimal FastAPI app mounts the router with the control bound from a small
injector — the same harness shape the bots endpoints use — so what is pinned
here is the wire contract OCB scripts depend on: the consent page's 200, the
listing and its ``bot_id`` filter, the approve/reject round trip, and the 404 /
409 errors that give a script (or a human in an iframe) its navigation.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Binder, Injector, Module

from agentclaw.community.adapters.http.simulator.agentpass import router
from agentclaw.community.plugin_api.passport import PassportManualConsentControl
from agentclaw.community.plugins.community.passport_consent import (
    ManualConsentPassportPlugin,
    NullPassportManualConsentControl,
)

_BASE_URL = "http://backend-simulator:31231"


class _M(Module):
    def __init__(self, control: PassportManualConsentControl):
        self._control = control

    def configure(self, binder: Binder) -> None:
        binder.bind(PassportManualConsentControl, to=self._control)


def _client(control: PassportManualConsentControl) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([_M(control)]))
    return TestClient(app)


def _register(plugin: ManualConsentPassportPlugin, bot_id: str = "sim-bot-1"):
    result = plugin.apply_agent_passport(
        bot_id=bot_id,
        owner_workno="workno-1",
        mcp_codes=[],
        bot_name="Simulator Bot",
        bot_desc="a route-test fixture bot",
        engine_type="openclaw",
    )
    return result["iframe_url"].rsplit("app=", 1)[1]


@pytest.fixture
def manual_client() -> TestClient:
    return _client(ManualConsentPassportPlugin(consent_base_url=_BASE_URL))


def test_null_control_composition_answers_404_on_every_surface():
    client = _client(NullPassportManualConsentControl())
    assert client.get("/simulator/agentpass/consent?app=x").status_code == 404
    assert client.get("/simulator/agentpass/applications").status_code == 404
    assert client.get("/simulator/agentpass/applications/x").status_code == 404
    response = client.post(
        "/simulator/agentpass/applications/x/decision", json={"approve": True}
    )
    assert response.status_code == 404
    assert "manual_consent" in response.json()["detail"]


def test_unbound_control_composition_answers_404():
    app = FastAPI()
    app.include_router(router)
    attach_injector(app, Injector([]))
    client = TestClient(app)
    assert client.get("/simulator/agentpass/applications").status_code == 404


def test_consent_page_serves_the_application_facts_as_html(manual_client):
    plugin: ManualConsentPassportPlugin = manual_client.app.state.injector.get(
        PassportManualConsentControl
    )
    application_id = _register(plugin)

    response = manual_client.get(f"/simulator/agentpass/consent?app={application_id}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Simulator Bot" in body
    assert "sim-bot-1" in body
    assert application_id in body
    # No external assets: everything the page needs is inside this response.
    assert "<script src=" not in body
    assert 'src="http' not in body
    assert 'href="http' not in body


def test_consent_page_for_an_already_decided_application_still_renders(
    manual_client,
):
    plugin: ManualConsentPassportPlugin = manual_client.app.state.injector.get(
        PassportManualConsentControl
    )
    application_id = _register(plugin)
    plugin.decide(application_id, approve=True)

    response = manual_client.get(f"/simulator/agentpass/consent?app={application_id}")

    assert response.status_code == 200
    assert "already decided" in response.text


def test_consent_page_for_unknown_application_is_404(manual_client):
    response = manual_client.get("/simulator/agentpass/consent?app=sim-passport-none")
    assert response.status_code == 404
    assert "unknown passport application" == response.json()["detail"]


def test_applications_listing_and_bot_id_filter(manual_client):
    plugin: ManualConsentPassportPlugin = manual_client.app.state.injector.get(
        PassportManualConsentControl
    )
    _register(plugin, bot_id="bot-alpha")
    _register(plugin, bot_id="bot-beta")

    everything = manual_client.get("/simulator/agentpass/applications")
    assert everything.status_code == 200
    items = everything.json()["items"]
    assert sorted(item["bot_id"] for item in items) == ["bot-alpha", "bot-beta"]
    assert set(items[0]) >= {
        "application_id",
        "bot_id",
        "owner_workno",
        "status",
        "created_at",
    }

    filtered = manual_client.get(
        "/simulator/agentpass/applications", params={"bot_id": "bot-alpha"}
    )
    assert [item["bot_id"] for item in filtered.json()["items"]] == ["bot-alpha"]


def test_application_detail_serves_the_facts_json(manual_client):
    plugin: ManualConsentPassportPlugin = manual_client.app.state.injector.get(
        PassportManualConsentControl
    )
    application_id = _register(plugin, bot_id="bot-detail")

    response = manual_client.get(f"/simulator/agentpass/applications/{application_id}")

    assert response.status_code == 200
    facts = response.json()
    assert facts["application_id"] == application_id
    assert facts["bot_id"] == "bot-detail"
    assert facts["status"] == "PENDING"
    assert facts["bot_name"] == "Simulator Bot"

    missing = manual_client.get("/simulator/agentpass/applications/sim-passport-none")
    assert missing.status_code == 404


def test_decision_round_trip_approve_then_reject(manual_client):
    plugin: ManualConsentPassportPlugin = manual_client.app.state.injector.get(
        PassportManualConsentControl
    )
    approve_id = _register(plugin, bot_id="bot-approve")
    reject_id = _register(plugin, bot_id="bot-reject")

    approved = manual_client.post(
        f"/simulator/agentpass/applications/{approve_id}/decision",
        json={"approve": True, "reason": "go ahead"},
    )
    assert approved.status_code == 200
    assert approved.json() == {
        "application_id": approve_id,
        "status": "ISSUED",
    }

    rejected = manual_client.post(
        f"/simulator/agentpass/applications/{reject_id}/decision",
        json={"approve": False, "reason": "not mine"},
    )
    assert rejected.status_code == 200
    assert rejected.json() == {
        "application_id": reject_id,
        "status": "REJECTED",
    }

    assert plugin.query_auth_status("bot-approve", "workno-1") == {
        "status": "ISSUED",
        "token": "community-passport-bot-approve",
    }
    assert plugin.query_auth_status("bot-reject", "workno-1") == {
        "status": "REJECTED",
    }


def test_decision_on_unknown_application_is_404(manual_client):
    response = manual_client.post(
        "/simulator/agentpass/applications/sim-passport-none/decision",
        json={"approve": True},
    )
    assert response.status_code == 404


def test_decision_on_a_decided_application_is_409(manual_client):
    plugin: ManualConsentPassportPlugin = manual_client.app.state.injector.get(
        PassportManualConsentControl
    )
    application_id = _register(plugin, bot_id="bot-once")
    first = manual_client.post(
        f"/simulator/agentpass/applications/{application_id}/decision",
        json={"approve": True},
    )
    assert first.status_code == 200

    second = manual_client.post(
        f"/simulator/agentpass/applications/{application_id}/decision",
        json={"approve": False},
    )

    assert second.status_code == 409
    assert "already decided" in second.json()["detail"]
    assert plugin.query_auth_status("bot-once", "workno-1")["status"] == "ISSUED"


def test_decision_body_requires_the_approve_flag(manual_client):
    response = manual_client.post(
        "/simulator/agentpass/applications/any/decision", json={}
    )
    assert response.status_code == 422


def test_router_is_mounted_in_the_community_app_assembly():
    from agentclaw.community.adapters.http.app import app as community_app

    paths = set(community_app.openapi()["paths"])
    assert "/simulator/agentpass/consent" in paths
    assert "/simulator/agentpass/applications" in paths
    assert "/simulator/agentpass/applications/{application_id}" in paths
    assert "/simulator/agentpass/applications/{application_id}/decision" in paths


def test_default_composition_serves_404_on_every_simulator_surface(client):
    """The only default-mode delta through the assembled app: a 404 router."""
    assert client.get("/simulator/agentpass/applications").status_code == 404
    response = client.get("/simulator/agentpass/consent?app=sim-passport-none")
    assert response.status_code == 404
    assert "manual_consent" in response.json()["detail"]