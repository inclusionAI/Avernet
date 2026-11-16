"""Catalog pagination filters service runtimes without changing personal Bots."""

from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_public.catalog_metadata import (
    BotCatalogAddress, BotCatalogCaller, BotCatalogMetadata, BotCatalogMetadataPage,
)
from tests.community.core.bot_public.test_bot_public_service import _make_service
from tests.community.repository.bot.test_stage_catalog import catalog as catalog, bot, publish


def _catalog(rows):
    metadata, repository = MagicMock(), MagicMock()

    def search(**kwargs):
        start = (kwargs["page"] - 1) * kwargs["page_size"]
        return BotCatalogMetadataPage(total=len(rows), items=[
            BotCatalogMetadata(BotCatalogAddress(row["bot_id"], row["owner_id"]), "bot")
            for row in rows[start:start + kwargs["page_size"]]
        ])

    def join(pairs, **kwargs):
        matching = [row.copy() for row in rows if (row["bot_id"], row["owner_id"]) in pairs]
        return matching

    metadata.search_public_bot_metadata.side_effect = search
    repository.list_catalog_bots_by_owner_bot_pairs.side_effect = join
    return _make_service(bot_repository=repository, catalog_metadata_service=metadata), metadata, repository


def _row(index, *, kind="service", available=False, owner="owner"):
    return {
        "bot_id": f"bot-{index}", "owner_id": owner, "bot_type": kind,
        "status": "ACTIVE", "online_available": available,
    }


def _search(service, page=1, page_size=24):
    return service.search_catalog_public_bots_by_keyword(
        caller=BotCatalogCaller("tenant", "viewer", None), request_id="test",
        page=page, page_size=page_size,
    )


def test_service_bots_require_online_publication_but_personal_bots_do_not():
    service, _, _ = _catalog([
        _row("offline"), _row("unbound"), _row("draft"),
        _row("live", available=True), _row("personal", kind="personal"),
    ])
    result = _search(service)
    assert result["total"] == 2
    assert [row["bot_id"] for row in result["items"]] == ["bot-live", "bot-personal"]


@pytest.mark.parametrize("page", [1, 2, 3, 4])
def test_filtering_fills_pages_and_counts_across_bounded_bcs_batches(page):
    rows = [_row(i, available=i % 4 == 0) for i in range(245)]
    service, metadata, repository = _catalog(rows)
    result = _search(service, page=page)
    eligible = [row["bot_id"] for row in rows if row["online_available"]]
    assert result["total"] == len(eligible)
    assert [row["bot_id"] for row in result["items"]] == eligible[(page - 1) * 24:page * 24]
    assert metadata.search_public_bot_metadata.call_count == 3
    assert repository.list_catalog_bots_by_owner_bot_pairs.call_count == 3
    assert all(call.kwargs["page_size"] == 100 for call in metadata.search_public_bot_metadata.call_args_list)
    assert all(len(call.args[0]) <= 100 for call in repository.list_catalog_bots_by_owner_bot_pairs.call_args_list)


def test_same_bot_id_under_other_owner_cannot_supply_online_availability():
    service, _, _ = _catalog([
        _row("shared", owner="draft-owner"),
        _row("shared", owner="live-owner", available=True),
    ])
    result = _search(service)
    assert result["total"] == 1
    assert result["items"][0]["owner_id"] == "live-owner"


def test_real_publications_exclude_offline_unbound_wrong_env_and_do_not_fall_back(catalog):
    repository, database = catalog
    rows = [bot(repository, name) for name in ("live", "offline", "unbound", "wrong-env", "new-unbound")]
    publish(database, rows[0], bindings={"online": 71})
    publish(database, rows[1], status="offline", bindings={"online": 72})
    publish(database, rows[2])
    publish(database, rows[3], bindings={"online": 73}, env="other")
    publish(database, rows[4], bindings={"online": 74})
    publish(database, rows[4], version=2)
    personal = bot(repository, "personal", kind="personal")
    metadata = MagicMock()
    metadata.search_public_bot_metadata.return_value = BotCatalogMetadataPage(
        total=6, items=[BotCatalogMetadata(BotCatalogAddress(row["bot_id"], row["owner_id"]), "bot")
                        for row in [*rows, personal]],
    )
    service = _make_service(bot_repository=repository, catalog_metadata_service=metadata)
    result = _search(service)
    assert result["total"] == 2
    assert [row["bot_id"] for row in result["items"]] == ["live", "personal"]
