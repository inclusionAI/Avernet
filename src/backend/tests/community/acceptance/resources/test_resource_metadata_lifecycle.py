"""Real singlebox lifecycle for URL, Node, and Link resource metadata."""

from __future__ import annotations

import httpx
import pytest

from tests.community.acceptance._fixtures.live_personal_bot import (
    create_live_personal_bot,
    fresh_id,
)


def _assert_success(response: httpx.Response) -> dict:
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["success"] is True, payload
    return payload


@pytest.mark.acceptance
def test_resource_metadata_roundtrip(live_backend):
    """Manage local resource metadata without calling remote knowledge systems."""
    user_id = fresh_id("e2e_resources_user")
    headers = {"x-user-id": user_id}

    with httpx.Client(base_url=live_backend, headers=headers, timeout=60.0) as client:
        bot = create_live_personal_bot(
            client,
            user_id=user_id,
            bot_name_prefix="Resources",
            bot_desc="resource metadata lifecycle bot",
        )
        bot_id = bot["bot_id"]

        url_payload = _assert_success(
            client.post(
                f"/api/resources/url?bot_id={bot_id}",
                json={
                    "name": "Project handbook",
                    "url": "https://example.com/handbook",
                },
            )
        )
        url_id = url_payload["data"]["id"]

        node_payload = _assert_success(
            client.post(
                f"/api/resources/node?bot_id={bot_id}",
                json={
                    "name": "Dataset node",
                    "node_address": "ipfs://singlebox-dataset",
                },
            )
        )
        node_id = node_payload["data"]["id"]

        response = client.get(
            f"/api/resources/check-name?bot_id={bot_id}"
            "&resource_type=url&name=Project%20handbook"
        )
        assert _assert_success(response)["data"]["available"] is False

        listed = _assert_success(
            client.get(
                f"/api/resources?bot_id={bot_id}&owner_id={user_id}&search=Project"
            )
        )
        assert [item["id"] for item in listed["data"]] == [url_id]

        link_payload = _assert_success(
            client.post(
                f"/api/resources/links?bot_id={bot_id}",
                json={
                    "links": {
                        "dima": [
                            {
                                "name": "DIMA task",
                                "url": "https://example.com/dima/task/42",
                            }
                        ]
                    }
                },
            )
        )
        link_id = link_payload["data"][0]["id"]

        updated = _assert_success(
            client.put(
                f"/api/resources/links/{link_id}?bot_id={bot_id}",
                json={
                    "link_type": "antcode",
                    "name": "Avernet source",
                    "url": "https://github.com/inclusionAI/Avernet",
                },
            )
        )
        assert updated["data"]["link_type"] == "antcode"
        assert updated["data"]["name"] == "Avernet source"

        detail = _assert_success(
            client.get(f"/api/resources/{link_id}?bot_id={bot_id}")
        )
        assert detail["data"]["url"] == "https://github.com/inclusionAI/Avernet"

        deleted = _assert_success(
            client.delete(
                f"/api/resources/{link_id}?bot_id={bot_id}&entity_id={user_id}"
            )
        )
        assert deleted["message"] == "Resource deleted"

        deleted_detail = _assert_success(
            client.get(f"/api/resources/{link_id}?bot_id={bot_id}")
        )
        assert deleted_detail["data"]["status"] == "deleted"

        remaining = _assert_success(
            client.get(f"/api/resources?bot_id={bot_id}&owner_id={user_id}")
        )
        assert {item["id"] for item in remaining["data"]} == {url_id, node_id}
