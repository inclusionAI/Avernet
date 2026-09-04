"""BCSFuse ranking joined with the authoritative BCS Bot catalog."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress,
    BotCatalogCaller,
    BotCatalogMetadata,
    BotCatalogMetadataPage,
    BotCatalogSearchFilters,
)
from agentclaw.community.core.bot_public.services.bot_discover_service import (
    BotDiscoverService,
)


def _backend_bot(*, public: str = "0") -> dict[str, Any]:
    return {
        "id": 1,
        "bot_id": "catalog-bot",
        "bot_type": "service",
        "bot_name": "Catalog Bot",
        "bot_desc": "BCS-visible bot",
        "entity_id": "owner-1",
        "entity_type": "user",
        "creator_id": "owner-1",
        "owner_id": "owner-1",
        "engine_types": ["openclaw"],
        "status": "ACTIVE",
        "binding_id": None,
        "gmt_create": 1,
        "gmt_modified": 2,
        "modifier_id": "owner-1",
        "share_policy": None,
        "is_delete": 0,
        "active_engine": "openclaw",
        "device_id": "secret-device",
        "env": "pre",
        "ext": {"owner_name": "Owner"},
        "public": public,
    }


@dataclass
class _Repo:
    calls: list[tuple[list[tuple[str, str]], int, int]] = field(default_factory=list)

    def list_bots_by_owner_bot_pairs(
        self,
        pairs: list[tuple[str, str]],
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict[str, Any]]]:
        self.calls.append((pairs, page, page_size))
        return 1, [_backend_bot(public="0")]

    def list_public_bots_by_owner_bot_pairs(self, _pairs):
        raise AssertionError("Discover must not use the legacy ac_bots.public filter")


@dataclass
class _Metadata:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def search_public_bot_metadata(self, **kwargs: Any) -> BotCatalogMetadataPage:
        self.calls.append(kwargs)
        return BotCatalogMetadataPage(
            total=1,
            items=[
                BotCatalogMetadata(
                    address=BotCatalogAddress("catalog-bot", "owner-1"),
                    kind="bot",
                    bot_uuid="catalog-bot:owner-1",
                    visibility="private",
                    user_visibility="public",
                    actor_kind="bot",
                    is_online=True,
                )
            ],
        )


def test_discover_uses_bcs_visibility_and_not_legacy_backend_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A human-visible BCS Bot remains discoverable when ac_bots.public is stale."""
    repo = _Repo()
    metadata = _Metadata()
    service = BotDiscoverService(
        bot_repository=repo,
        bcsfuse_config=SimpleNamespace(
            base_url="http://bcsfuse.test", base_url_pre=None
        ),
        catalog_metadata_service=metadata,
    )
    recommendations = [
        {"worker_id": "catalog-bot:owner-1", "score": 0.9, "reasons": ["match"]},
        {"worker_id": "default:owner-1", "score": 0.8, "reasons": ["stale"]},
    ]
    monkeypatch.setattr(
        service,
        "_call_bcsfuse_recommend",
        lambda **_kwargs: {"recommendations": recommendations},
    )
    caller = BotCatalogCaller("teamclaw", "owner-1", None)
    filters = BotCatalogSearchFilters(
        status="online", viewer_actor_type="human", viewer_actor_id="owner-1"
    )

    result = service.search_by_keyword(
        keyword="研发",
        top_k=20,
        min_score=0.1,
        filters={"runtime_state": ["online"]},
        catalog_filters=filters,
        caller=caller,
        request_id="trace-discover",
    )

    assert result["total"] == 1
    assert result["items"][0]["bot_uuid"] == "catalog-bot:owner-1"
    assert result["items"][0]["visibility"] == "private"
    assert result["items"][0]["user_visibility"] == "public"
    assert result["items"][0]["recommend"]["score"] == 0.9
    assert repo.calls == [([("catalog-bot", "owner-1")], 1, 1)]
    assert metadata.calls == [
        {
            "search": None,
            "page": 1,
            "page_size": 2,
            "bot_uuids": ("catalog-bot:owner-1", "default:owner-1"),
            "filters": filters,
            "caller": caller,
            "request_id": "trace-discover",
        }
    ]
