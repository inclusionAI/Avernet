"""Identity source end to end over the HTTP surface.

The service tests prove the seam decides correctly; this file proves the
decision reaches a caller. A submit is posted to the real agentswarm router
over the composed application (business modules plus the profile's
infrastructure column), with the Bot rows in the profile's database, so a
missing binding or an unreachable row fails here rather than in production.

The case that motivated the change: a Bot created before `ac_bots.ext`
carried a passport copy, whose AgentPass still exists at the passport
provider. Before this change the submit was refused with 422 on the false
claim that the Bot holds no issued passport.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector

from agentclaw.community.core.agentswarm.config.settings import (
    AgentSwarmConfig, LlmConfig,
)
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugin_api.models import BotModel

OWNER = "20231101-old-bot-owner"
OTHER_OWNER = "someone-else"
OLD_BOT = "20231101_old_bot"
TENANT = "tenant-identity-source"


@pytest.fixture
def app_client(test_injector):
    """The composed app, with the schema created and a reachable LLM block.

    The LLM endpoint is deliberately unreachable: the run is admitted and
    then fails its own dispatch, which is all this file needs — the
    identity check runs before any row is written.
    """
    from agentclaw.community.adapters.http.app import app

    test_injector.binder.bind(
        AgentSwarmConfig,
        to=AgentSwarmConfig(
            enabled=True,
            llm=LlmConfig(base_url="http://127.0.0.1:9/v1", api_key="k",
                          default_model="m"),
        ),
    )
    asyncio.run(test_injector.get(DatabasePlugin).bootstrap())
    previous = getattr(app.state, "injector", None)
    attach_injector(app, test_injector)
    try:
        yield TestClient(app), test_injector.get(DatabasePlugin)
    finally:
        if previous is not None:
            attach_injector(app, previous)


def _bot(db, *, bot_id: str, owner: str, ext: str | None = None) -> None:
    with db.orm_session() as session:
        session.add(BotModel(
            bot_id=bot_id, bot_name=bot_id, entity_id=owner,
            entity_type="user", creator_id=owner, owner_id=owner, ext=ext,
        ))


def _submit(client, *, owner: str, identity_bot_id: str):
    return client.post(
        "/openapi/v1/agentswarm/runs",
        json={
            "tenant": TENANT,
            "goal": "analyze the build logs",
            "agents": [{"name": "r1", "role": "research", "kind": "bot"}],
            "identity_bot_id": identity_bot_id,
        },
        headers={"x-user-id": owner},
    )


class TestAnOlderBotIsUsableAsAnIdentitySource:

    def test_a_bot_with_no_cached_passport_submits_successfully(self, app_client):
        """The whole point of the change, over HTTP."""
        client, db = app_client
        _bot(db, bot_id=OLD_BOT, owner=OWNER, ext=None)

        resp = _submit(client, owner=OWNER, identity_bot_id=OLD_BOT)

        assert resp.status_code == 200, resp.text
        assert resp.json()["run_id"]

    def test_a_bot_whose_ext_carries_no_passport_submits_successfully(
            self, app_client):
        """A row with an ext blob that simply never got a passport entry."""
        client, db = app_client
        _bot(db, bot_id=OLD_BOT, owner=OWNER, ext='{"other": true}')

        resp = _submit(client, owner=OWNER, identity_bot_id=OLD_BOT)

        assert resp.status_code == 200, resp.text


class TestTheRefusalsStillHold:

    def test_an_unowned_bot_is_refused_as_not_owned(self, app_client):
        client, db = app_client
        _bot(db, bot_id=OLD_BOT, owner=OTHER_OWNER, ext=None)

        resp = _submit(client, owner=OWNER, identity_bot_id=OLD_BOT)

        assert resp.status_code == 422
        error = resp.json()["error"]
        assert "not a Bot owned by the caller" in error
        # Not a passport verdict the caller cannot confirm.
        assert "holds no issued passport" not in error

    def test_a_bot_that_does_not_exist_is_refused(self, app_client):
        client, _ = app_client

        resp = _submit(client, owner=OWNER, identity_bot_id="never-existed")

        assert resp.status_code == 422
        assert "not a Bot owned by the caller" in resp.json()["error"]

    def test_a_pending_cached_passport_is_refused(self, app_client):
        """The cache answered, so the provider is never consulted."""
        client, db = app_client
        _bot(db, bot_id=OLD_BOT, owner=OWNER, ext=(
            '{"passport": {"agent_code": "cached-code", "status": "PENDING"}}'
        ))

        resp = _submit(client, owner=OWNER, identity_bot_id=OLD_BOT)

        assert resp.status_code == 422
        assert "pending authorization" in resp.json()["error"]