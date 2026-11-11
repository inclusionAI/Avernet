"""Authenticated HTTP -> scoped Bot SQL -> catalog transport display snapshots."""

from unittest.mock import Mock

import httpx
import pytest

from .conftest import make_catalog
from .http_contract import EcbContract, detail


@pytest.mark.parametrize("granted", [True, False])
def test_grant_forwards_scoped_bot_name_without_extra_reads(client, world, granted):
    upstream = EcbContract()
    upstream.reply(
        "GET",
        "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases/kb/catalog-detail",
        detail(),
        params={"actor_staff_no": "actor"},
    )
    upstream.reply(
        "PUT",
        "/api/v1/knowledge/kb/kb/bot-authorizations/teamclaw/bot",
        {"granted": granted, "revision": 3},
        body={
            "actor_staff_no": "actor",
            "user_id": "owner",
            "bot_name": "Real bot",
            "granted": granted,
            "expected_revision": 2,
        },
    )
    world.source.display = Mock(wraps=world.source.display)
    with httpx.Client(transport=httpx.MockTransport(upstream.send)) as transport:
        world.service.catalog = make_catalog(
            "https://ecb.example", enabled=True, client=transport
        )
        response = client.put(
            "/api/knowledge/spaces/1/knowledge-bases/kb/bot-authorizations/bot",
            json={
                "target_owner_id": "owner",
                "granted": granted,
                "expected_revision": 2,
            },
        )
    assert response.status_code == 200
    assert response.json() == {"granted": granted, "revision": 3}
    assert len(upstream.requests) == 2
    assert not upstream.errors
    world.source.display.assert_called_once_with(1, [("bot", "owner")])


def test_browser_cannot_supply_activity_bot_name(client, world):
    response = client.put(
        "/api/knowledge/spaces/1/knowledge-bases/kb/bot-authorizations/bot",
        json={
            "target_owner_id": "owner",
            "granted": True,
            "expected_revision": 0,
            "bot_name": "Spoofed name",
        },
    )
    assert response.status_code == 422
    assert world.catalog.calls == []
