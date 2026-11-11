"""Hosts can isolate business Cards without changing the default catalog."""

import sqlite3

import pytest
from search_core.providers.card_schema import initialize_sqlite_cards
from search_core.providers.sql_catalog import (
    SqlCatalogRepository,
    sqlite_catalog_repository,
)


def test_custom_source_table_does_not_read_or_write_default_cards(tmp_path):
    path = str(tmp_path / "shared.sqlite3")
    default = sqlite_catalog_repository(path)
    tc = sqlite_catalog_repository(path, table="bcsfuse_tc_agent_cards")
    default.upsert(
        "same-id", {"name": "Alipay"}, resource_type="agent", search_config={}
    )
    tc.upsert("same-id", {"name": "TC"}, resource_type="agent", search_config={})
    tc.remove("same-id")
    assert default.get("same-id").card.source_card["name"] == "Alipay"
    assert not default.get("same-id").card.deleted
    assert tc.get("same-id").card.deleted
    assert default.table == "bcsfuse_agent_cards"


def test_delete_retires_only_its_business_docs_in_all_generations(tmp_path):
    from search_core.catalog.updates import CardUpdates
    from search_core.providers.demo_embedding import DemoEmbedding
    from search_core.providers.qdrant_index import QdrantRetrievalIndex

    path = str(tmp_path / "shared.sqlite3")
    alipay = sqlite_catalog_repository(path)
    tc = sqlite_catalog_repository(
        path, table="bcsfuse_tc_agent_cards", document_table="tc_search_index_documents"
    )
    model = DemoEmbedding()
    indexes = []
    try:
        for repository in (alipay, tc):
            repository.upsert(
                "same-id",
                {"name": "Current business"},
                resource_type="agent",
                search_config={},
            )
            for key in ("model-old", "model-current"):
                index = QdrantRetrievalIndex(
                    path=str(tmp_path / f"{repository.table}-{key}"),
                    dimension=model.dimension,
                )
                indexes.append(index)
                CardUpdates(
                    repository,
                    model,
                    index,
                    embedding_key=key,
                    instance_id=key,
                    index_key=key,
                ).run_once()
                assert (
                    not repository.index_documents(key).load_by_id("same-id").is_deleted
                )
        tc.remove("same-id")
        for key in ("model-old", "model-current"):
            assert tc.index_documents(key).load_by_id("same-id").is_deleted
            assert not alipay.index_documents(key).load_by_id("same-id").is_deleted
        # A later Alipay generation may still reuse its own unmodified artifacts.
        assert (
            not alipay.index_documents("new-model")
            .load_latest_by_id("same-id")
            .is_deleted
        )
    finally:
        for index in indexes:
            index.close()


@pytest.mark.parametrize(
    "table", ["", "cards; DROP TABLE x", "a.b", "a-b", "cards WHERE 1=1", "1cards"]
)
def test_table_names_are_validated_before_sql(table):
    with pytest.raises(ValueError, match="table"):
        SqlCatalogRepository(lambda: None, table=table)
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(ValueError, match="table"):
            initialize_sqlite_cards(connection, table=table)
    finally:
        connection.close()


def test_legacy_host_mixin_keeps_the_default_doc_table(tmp_path):
    from search_core.providers.sql_card_preparation import CardPreparation
    from search_core.providers.sql_cards import SqlCardRepository

    class LegacyHostRepository(CardPreparation, SqlCardRepository):
        pass

    original = sqlite_catalog_repository(str(tmp_path / "cards.sqlite3"))
    host = LegacyHostRepository(
        original.connect, table=original.table, environment="local", placeholder="?"
    )
    host.upsert(
        "existing", {"name": "Host card"}, resource_type="agent", search_config={}
    )
    assert host.index_documents("generation").table == "search_index_documents"
    assert host.index_documents("generation").load_by_id("existing") is None
