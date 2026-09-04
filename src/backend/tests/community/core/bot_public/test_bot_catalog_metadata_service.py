"""BCS-backed catalog metadata adapter behavior."""

from __future__ import annotations

import importlib
from unittest.mock import Mock

import httpx
import pytest

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataServiceProtocol,
    BotCatalogMetadataUnavailableError,
    BotCatalogMetadataPage,
    BotCatalogSearchFilters,
)
from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugins.local.http_client import LocalHttpClient


def _response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        request=httpx.Request("GET", "http://bcs.test/bots/search"),
    )


def _caller() -> BotCatalogCaller:
    return BotCatalogCaller(tenant_id="tenant-1", user_id="user-1", app_id=9)


def _service_class():
    module = importlib.import_module(
        "agentclaw.community.core.bot_public.services.bot_catalog_metadata_service"
    )
    service_class = getattr(module, "BcsBotCatalogMetadataService", None)
    assert service_class is not None, "BCS catalog adapter must be available"
    return service_class


def _make_service() -> tuple[BotCatalogMetadataServiceProtocol, LocalHttpClient]:
    http = LocalHttpClient(base_url="http://bcs.test")
    return _service_class()(http_client=http), http


def test_bcs_catalog_search_maps_current_page_and_parses_exact_address() -> None:
    """A wrong page mapping or UUID split would return the wrong catalog page."""
    service, http = _make_service()
    http.set_response(
        "get",
        _response(
            200,
            {
                "total": 21,
                "items": [
                    {
                        "bot_uuid": " bot-1 : owner-1 ",
                        "actor_kind": "bot",
                        "name": "ignored-by-backend",
                    }
                ],
            },
        ),
    )

    result = service.search_public_bot_metadata(
        search="agent", page=2, page_size=20, caller=_caller(), request_id="trace-1"
    )

    assert result == BotCatalogMetadataPage(
        total=21,
        items=[
            BotCatalogMetadata(
                BotCatalogAddress("bot-1", "owner-1"),
                "bot",
                bot_uuid=" bot-1 : owner-1 ",
                actor_kind="bot",
            )
        ],
    )
    call = http.calls_to("get")[0]
    assert call.args[0] == "/bots/search"
    assert call.kwargs["params"] == {
        "q": "agent",
        "offset": 20,
        "limit": 20,
        "tc_bot": True,
    }
    assert "headers" not in call.kwargs


def test_bcs_catalog_search_preserves_optional_is_friend_false() -> None:
    """A BCS false value is meaningful and must not be dropped as falsy."""
    service, http = _make_service()
    http.set_response(
        "get",
        _response(
                200,
                {
                    "total": 1,
                    "items": [
                    {
                        "bot_uuid": "bot-1:owner-1",
                        "actor_kind": "bot",
                        "is_friend": False,
                    }
                ]
            },
        ),
    )

    result = service.search_public_bot_metadata(
        search=None, page=1, page_size=20, caller=_caller(), request_id="trace-friend"
    )

    assert result.total == 1
    assert getattr(result.items[0], "is_friend", None) is False


def test_bcs_catalog_search_preserves_requested_optional_metadata_fields() -> None:
    """Catalog Search must retain the explicitly supported BCS item fields."""
    service, http = _make_service()
    friend_ext = {
        "public_user_approval": {
            "status": "PROCESSING",
            "view_friend_deps": [{"deptNo": "D1"}],
        }
    }
    friend_check_in_strategy = {}
    http.set_response(
        "get",
        _response(
                200,
                {
                    "total": 1,
                    "items": [
                    {
                        "bot_uuid": "bot-1:owner-1",
                        "actor_kind": "bot",
                        "visibility": "protected",
                        "is_online": False,
                        "is_friend": False,
                        "friend_ext": friend_ext,
                        "friend_check_in_strategy": friend_check_in_strategy,
                        "user_visibility": "private",
                    }
                ]
            },
        ),
    )

    result = service.search_public_bot_metadata(
        search=None, page=1, page_size=20, caller=_caller(), request_id="trace-metadata"
    )

    assert result == BotCatalogMetadataPage(
        total=1,
        items=[
            BotCatalogMetadata(
                BotCatalogAddress("bot-1", "owner-1"),
                "bot",
                bot_uuid="bot-1:owner-1",
                is_friend=False,
                visibility="protected",
                is_online=False,
                actor_kind="bot",
                friend_ext=friend_ext,
                friend_check_in_strategy=friend_check_in_strategy,
                user_visibility="private",
            )
        ],
    )


def test_bcs_catalog_search_omits_blank_query_and_keeps_page_boundary() -> None:
    """Listing all catalog Bots must not turn a blank query into a BCS filter."""
    service, http = _make_service()
    http.set_response("get", _response(200, {"total": 0, "items": []}))

    result = service.search_public_bot_metadata(
        search=" ", page=3, page_size=10, caller=_caller(), request_id="trace-2"
    )

    assert result == BotCatalogMetadataPage(total=0, items=[])
    assert http.calls_to("get")[0].kwargs["params"] == {
        "offset": 20,
        "limit": 10,
        "tc_bot": True,
    }


@pytest.mark.parametrize("total", [True, -1, "1", None])
def test_bcs_catalog_search_rejects_invalid_total(total: object) -> None:
    service, http = _make_service()
    http.set_response("get", _response(200, {"total": total, "items": []}))

    with pytest.raises(BotCatalogMetadataUnavailableError):
        service.search_public_bot_metadata(
            search=None, page=1, page_size=20, caller=_caller(), request_id="trace-total"
        )


def test_bcs_catalog_search_forwards_only_supplied_frontend_filters() -> None:
    """The adapter must preserve the explicit BCS filter contract without headers."""
    service, http = _make_service()
    http.set_response("get", _response(200, {"total": 0, "items": []}))

    result = service.search_public_bot_metadata(
        search=None,
        page=2,
        page_size=10,
        filters=BotCatalogSearchFilters(
            visibility=("public", "protected"),
            user_visibility=("private",),
            status="online",
            viewer_actor_type="bot",
            viewer_actor_id="viewer:owner",
            friendship="non_friends",
        ),
        caller=_caller(),
        request_id="trace-filters",
    )

    assert result == BotCatalogMetadataPage(total=0, items=[])
    call = http.calls_to("get")[0]
    assert call.args[0] == "/bots/search"
    assert call.kwargs["params"] == {
        "offset": 10,
        "limit": 10,
        "tc_bot": True,
        "visibility": "public,protected",
        "user_visibility": "private",
        "status": "online",
        "viewer_actor_type": "bot",
        "viewer_actor_id": "viewer:owner",
        "friendship": "non_friends",
    }
    assert "headers" not in call.kwargs


def test_bcs_catalog_search_forwards_exact_bot_uuid_candidates() -> None:
    """Discover candidates must be checked by the same BCS catalog search path."""
    service, http = _make_service()
    http.set_response("get", _response(200, {"total": 0, "items": []}))

    result = service.search_public_bot_metadata(
        search=None,
        page=1,
        page_size=2,
        bot_uuids=("bot-1:owner-1", "bot-2:owner-2"),
        filters=BotCatalogSearchFilters(
            status="online",
            viewer_actor_type="human",
            viewer_actor_id="owner-1",
        ),
        caller=_caller(),
        request_id="trace-discover",
    )

    assert result == BotCatalogMetadataPage(total=0, items=[])
    assert http.calls_to("get")[0].kwargs["params"] == {
        "offset": 0,
        "limit": 2,
        "tc_bot": True,
        "bot_uuids": "bot-1:owner-1,bot-2:owner-2",
        "status": "online",
        "viewer_actor_type": "human",
        "viewer_actor_id": "owner-1",
    }


@pytest.mark.parametrize(
    "item",
    [
        {"bot_uuid": "bot-without-owner", "actor_kind": "bot"},
        {"bot_uuid": ":owner-1", "actor_kind": "bot"},
        {"bot_uuid": "bot-1:", "actor_kind": "bot"},
        {"bot_uuid": 1, "actor_kind": "bot"},
        {"bot_uuid": "bot-1:owner-1", "actor_kind": "human"},
        {"bot_uuid": "bot-1:owner-1", "actor_kind": "bot", "is_friend": "false"},
    ],
)
def test_bcs_catalog_search_fails_closed_for_invalid_item(
    item: dict[str, object],
) -> None:
    """Malformed or non-Bot BCS records must not produce partial public results."""
    service, http = _make_service()
    http.set_response("get", _response(200, {"total": 1, "items": [item]}))

    with pytest.raises(BotCatalogMetadataUnavailableError):
        service.search_public_bot_metadata(
            search="agent", page=1, page_size=20, caller=_caller(), request_id="trace-3"
        )


@pytest.mark.parametrize("payload", [{}, {"items": {}}])
def test_bcs_catalog_search_fails_closed_for_invalid_response_shape(
    payload: dict[str, object],
) -> None:
    """A BCS response without a list of records must not yield a partial page."""
    service, http = _make_service()
    http.set_response("get", _response(200, payload))

    with pytest.raises(BotCatalogMetadataUnavailableError):
        service.search_public_bot_metadata(
            search="agent", page=1, page_size=20, caller=_caller(), request_id="trace-shape"
        )


def test_bcs_catalog_search_fails_closed_for_malformed_json(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed BCS JSON stays unavailable and never logs the upstream detail."""
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.side_effect = ValueError("private malformed BCS body")
    service, http = _make_service()
    http.set_response("get", response)

    with caplog.at_level("WARNING"), pytest.raises(BotCatalogMetadataUnavailableError):
        service.search_public_bot_metadata(
            search="agent", page=1, page_size=20, caller=_caller(), request_id="trace-json"
        )

    assert "failure=upstream_unavailable" in caplog.text
    assert "private malformed BCS body" not in caplog.text


def test_bcs_catalog_search_fails_closed_for_duplicate_or_upstream_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Duplicate addresses and upstream details must not escape through the catalog."""
    service, http = _make_service()
    http.set_response(
        "get",
        _response(
            200,
            {
                "total": 2,
                "items": [
                    {"bot_uuid": "bot-1:owner-1", "actor_kind": "bot"},
                    {"bot_uuid": "bot-1:owner-1", "actor_kind": "bot"},
                ],
            },
        ),
    )

    with caplog.at_level("WARNING"), pytest.raises(BotCatalogMetadataUnavailableError):
        service.search_public_bot_metadata(
            search="agent", page=1, page_size=20, caller=_caller(), request_id="trace-4"
        )

    assert "failure=invalid_response" in caplog.text


def test_bcs_catalog_search_fails_closed_for_http_error() -> None:
    """A BCS failure must remain a fixed unavailable decision, not a Backend fallback."""
    service, http = _make_service()
    http.set_response("get", _response(503, {"message": "private upstream detail"}))

    with pytest.raises(BotCatalogMetadataUnavailableError):
        service.search_public_bot_metadata(
            search="agent", page=1, page_size=20, caller=_caller(), request_id="trace-5"
        )


def test_test_profile_binds_catalog_metadata_protocol_to_bcs_adapter() -> None:
    """The test profile must use the real adapter with a no-network HTTP seam."""
    port = build_injector(profile=DeployProfile.TEST).get(
        BotCatalogMetadataServiceProtocol
    )

    assert isinstance(port, _service_class())
