"""Trusted display names survive creation and durable manifest completion."""

import jwt
import pytest
from fastapi.testclient import TestClient

from agentclaw.community.adapters.http.openapi_v1.dependencies import PRINCIPAL_HEADER
from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogMetadata,
    BotCatalogMetadataPage,
    BotCatalogMetadataServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from tests.community.framework import bind_overrides
from tests.community.endpoints.test_openapi_bot_auth_status import (
    _KEY,
    _OWNER,
    _principal,
    _seed_for_completion,
)
from tests.community.endpoints.test_openapi_create_with_manifest import _Worker


@pytest.fixture
def client(app_with_testing_modules):
    return TestClient(app_with_testing_modules)


@pytest.mark.parametrize("flow", ["create", "post-auth", "get-auth", "manifest"])
@pytest.mark.parametrize(
    ("display_name", "expected"),
    [("  张三  ", "张三"), (None, _OWNER), ("", _OWNER), ("   ", _OWNER)],
)
def test_creation_persists_verified_owner_name(
    client, world, flow, display_name, expected
):
    _seed_for_completion(world)
    claims = jwt.decode(_principal(), _KEY, algorithms=["HS256"], audience="backend")
    claims["principals"][0]["subject"]["display_name"] = display_name
    headers = {PRINCIPAL_HEADER: jwt.encode(claims, _KEY, algorithm="HS256")}
    params = {"user_id": _OWNER}
    body = {
        "engine": "openclaw",
        "bot_name": "Owner Name Bot",
        "bot_desc": "owner regression",
        "cluster_name": "ACRA",
        "bot_type": "personal",
    }
    bot_id = "owner-name-bot"
    if flow == "create":
        response = client.post(
            "/openapi/v1/bots", params=params, headers=headers, json=body
        )
    elif flow == "manifest":
        response = client.post(
            "/openapi/v1/bots/with-manifest",
            params=params,
            headers=headers,
            json={**body, "config_manifest": "schema_version: 1\n"},
        )
    elif flow == "post-auth":
        response = client.post(
            f"/openapi/v1/bots/{bot_id}/auth-status",
            params=params,
            headers=headers,
            json=body,
        )
    else:
        response = client.get(
            f"/openapi/v1/bots/{bot_id}/auth-status",
            params={**params, **body},
            headers=headers,
        )
    assert response.status_code in (200, 201, 202), response.text
    if flow in ("create", "manifest"):
        bot_id = response.json()["data"]["bot_id"]
    if flow == "manifest":
        # Drive authorization until persistence, without waiting for a container.
        worker = _Worker(world)
        for _ in range(8):
            worker.turn()
            if world.get(BotRepository).get_by_id_and_entity(bot_id, _OWNER):
                break
    row = world.get(BotRepository).get_by_id_and_entity(bot_id, _OWNER)
    assert row is not None, response.text
    assert row["owner_name"] == expected
    assert row["owner_id"] == _OWNER
    assert row["entity_id"] == _OWNER

    def catalog_metadata(_self, **_kwargs):
        return BotCatalogMetadataPage(
            total=1,
            items=[BotCatalogMetadata(BotCatalogAddress(bot_id, _OWNER), kind="bot")],
        )

    bind_overrides(
        world,
        BotCatalogMetadataServiceProtocol,
        {"search_public_bot_metadata": catalog_metadata},
    )
    catalog = client.get("/openapi/v1/bots/catalog/search", headers=headers)
    assert catalog.status_code == 200, catalog.text
    items = catalog.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["owner_name"] == expected
    assert items[0]["entity_id"] == _OWNER


def test_manifest_completion_accepts_pre_upgrade_job_without_nickname(world):
    from agentclaw.community.core.bot_management.create_flow import (
        complete_manifest_creation,
    )
    from agentclaw.community.core.bot_management.services.bot_service import BotService
    from agentclaw.community.plugin_api.auth_relationship import AuthRelationshipPlugin
    from agentclaw.community.plugin_api.passport import PassportPlugin

    _seed_for_completion(world)
    complete_manifest_creation(
        {
            "user_id": _OWNER,
            "bot_id": "legacy-owner-name-bot",
            "spec": {
                "entity_id": _OWNER,
                "engine_type": "openclaw",
                "bot_type": "personal",
                "bot_name": "Legacy Job Bot",
                "template_validation_mode": "public",
                "deployment_mode": "cloud",
                "space_kind": "personal",
            },
        },
        bot_service=world.get(BotService),
        passport_plugin=world.get(PassportPlugin),
        auth_rel_plugin=world.get(AuthRelationshipPlugin),
        provision=False,
    )
    row = world.get(BotRepository).get_by_id_and_entity("legacy-owner-name-bot", _OWNER)
    assert row["owner_name"] == _OWNER


def test_create_rejects_owner_id_different_from_verified_user(client, world):
    _seed_for_completion(world)
    response = client.post(
        "/openapi/v1/bots",
        params={"user_id": "another-owner"},
        headers={PRINCIPAL_HEADER: _principal()},
        json={
            "engine": "openclaw",
            "bot_name": "Wrong Owner Bot",
            "bot_desc": "identity boundary",
            "cluster_name": "ACRA",
            "bot_type": "personal",
        },
    )
    assert response.status_code == 403, response.text
