"""Management catalog lifecycle facts remain provider-owned and optional."""

from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from agentclaw.community.adapters.http.knowledge_management.schemas import CatalogItem

from .http_contract import detail
from .test_catalog_batch import catalog_for


@pytest.mark.parametrize("count", [1, 20])
@pytest.mark.parametrize(
    "lifecycle",
    [
        {"status": "active", "delete_at_ms": None, "can_cancel_delete": False},
        {
            "status": "pending_delete",
            "delete_at_ms": 1795000000123,
            "can_cancel_delete": True,
        },
        {
            "status": "pending_delete",
            "delete_at_ms": 1795000000123,
            "can_cancel_delete": False,
        },
        {},  # Older providers do not supply lifecycle fields.
    ],
)
def test_catalog_preserves_optional_lifecycle_without_extra_calls(count, lifecycle):
    rows = [detail(kb=f"kb-{i}", **lifecycle) for i in range(count)]
    seen = []
    identities = SimpleNamespace(staff_no=Mock(return_value="mapped-actor"))

    def receive(request):
        seen.append(request)
        assert request.method == "GET"
        assert request.url.path == (
            "/api/v1/knowledge/integrations/tc/spaces/1/knowledge-bases"
        )
        assert dict(request.url.params) == {
            "actor_staff_no": "mapped-actor",
            "cursor": "previous-page",
            "limit": str(count),
        }
        return httpx.Response(
            200,
            json={
                "items": [dict(row, internal_cleanup_key="private") for row in rows],
                "next_cursor": "next-page",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(receive)) as client:
        page = catalog_for(client, identities).list_in_space(
            space_id=1, actor_id="actor", cursor="previous-page", limit=count
        )
    assert page == {"items": rows, "next_cursor": "next-page"}
    assert len(seen) == 1
    identities.staff_no.assert_called_once_with("actor")


def test_catalog_response_schema_documents_optional_lifecycle_fields():
    schema = CatalogItem.model_json_schema()
    for field in ("status", "delete_at_ms", "can_cancel_delete"):
        assert field in schema["properties"]
        assert field not in schema["required"]
