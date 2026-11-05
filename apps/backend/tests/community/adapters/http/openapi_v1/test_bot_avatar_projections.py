"""Display-only avatars must survive every conversation/discovery projection."""

import json

import pytest

from agentclaw.community.adapters.http.openapi_v1.bot_public.router import _public_bot
from agentclaw.community.adapters.http.openapi_v1.bots.converter_bot import _to_bot, _to_bot_metadata
from agentclaw.community.adapters.http.openapi_v1.collaborating_bots.router import _to_item
from agentclaw.community.core.bot_management.avatar import bot_avatar_url
from tests.community.adapters.http.openapi_v1.test_collaborating_bots import _relation
from tests.community.adapters.http.openapi_v1.test_catalog_stages import catalog_client as catalog_client

AVATAR = "/assets/bot-avatar.svg"


@pytest.mark.parametrize("ext", [{"avatar_url": AVATAR}, json.dumps({"avatar_url": AVATAR})])
def test_all_collection_projections_return_saved_avatar_without_extension_data(ext):
    bot = {"bot_id": "b1", "owner_id": "owner", "bot_type": "personal", "ext": ext}
    projections = [
        _to_bot(bot), _to_bot_metadata(bot), _public_bot(bot),
        _to_item(bot, _relation(), template_config=None, space=None),
    ]
    for result in projections:
        row = result.model_dump()
        assert row["avatar_url"] == AVATAR
        assert "ext" not in row


@pytest.mark.parametrize("ext", [None, "{bad", "[]", "null", 1, {"avatar_url": 42}, {"avatar_url": " "}])
def test_bad_or_missing_legacy_ext_does_not_break_avatar_projection(ext):
    assert bot_avatar_url({"ext": ext}) is None
    assert bot_avatar_url({"ext": ext, "avatar_url": AVATAR}) == AVATAR


def test_stored_extension_avatar_matches_inventory_precedence():
    assert bot_avatar_url({"ext": {"avatar_url": f" {AVATAR} "}, "avatar_url": "/old.png"}) == AVATAR


def test_owned_listing_returns_avatar_in_http_response(catalog_client):
    catalog_client.repository.list_by_conditions.return_value = (1, [{
        "bot_id": "avatar-bot", "owner_id": "owner", "ext": {"avatar_url": AVATAR},
    }])
    response = catalog_client.client.get("/openapi/v1/bots?stage=online")
    assert response.status_code == 200, response.json()
    assert response.json()["data"]["items"][0]["avatar_url"] == AVATAR


def test_metadata_batch_returns_avatar_without_owner_only_avatar_request(catalog_client):
    catalog_client.service.list_bots_by_owner_bot_pairs.return_value = {"total": 1, "items": [{
        "bot_id": "avatar-bot", "owner_id": "other", "ext": {"avatar_url": AVATAR},
    }]}
    response = catalog_client.client.post("/openapi/v1/bots/metadata/queries", json={
        "bots": [{"bot_id": "avatar-bot", "owner_id": "other"}],
    })
    assert response.status_code == 200, response.json()
    row = response.json()["data"]["items"][0]
    assert row["avatar_url"] == AVATAR
    assert "ext" not in row
