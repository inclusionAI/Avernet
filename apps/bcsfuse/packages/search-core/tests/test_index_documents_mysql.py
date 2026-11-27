"""Quoted identifiers work with SQLite and optional MySQL-compatible servers."""

import os
from dataclasses import replace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from search_core.plugin_api.index_documents import IndexDocument
from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.sql_index_documents import SqlIndexDocuments


def document():
    return IndexDocument(
        VectorPoint(
            id="doc",
            vector=[1.0, 0.0],
            payload={"source_id": "source", "content": "invoice"},
        ),
        1,
        {"embedding_key": "test/2", "text_hash": "hash"},
        filters={"visibility": ("public",)},
    )


@pytest.mark.parametrize("placeholder", ["?", "%s"])
@pytest.mark.parametrize("current", [None, (1, 1, 1000)])
def test_vector_identifier_is_quoted_in_reads_and_both_write_paths(
    placeholder, current
):
    store = SqlIndexDocuments(
        lambda: None, environment="test", index_key="one", placeholder=placeholder
    )
    assert "`vector`" in store.columns
    cur = Mock()
    cur.fetchone.side_effect = [current, (1001,)]
    assert store.save_in_transaction(cur, document())
    query = cur.execute.call_args.args[0]
    assert "`vector`" in query
    assert ("INSERT" if current is None else "UPDATE") in query
    # Values are still bound separately and never interpreted as SQL.
    assert "invoice" not in query


@pytest.fixture
def mysql_store():
    host = os.environ.get("SEARCH_CORE_TEST_MYSQL_HOST")
    if not host:
        pytest.skip(
            "set SEARCH_CORE_TEST_MYSQL_HOST for isolated MySQL/MariaDB integration"
        )
    connector = pytest.importorskip("mysql.connector")
    options = {
        "host": host,
        "port": int(os.environ.get("SEARCH_CORE_TEST_MYSQL_PORT", "3306")),
        "database": os.environ["SEARCH_CORE_TEST_MYSQL_DATABASE"],
        "user": os.environ["SEARCH_CORE_TEST_MYSQL_USER"],
        "password": os.environ["SEARCH_CORE_TEST_MYSQL_PASSWORD"],
        "use_pure": True,
        "charset": "utf8mb4",
        "collation": "utf8mb4_bin",
        "ssl_disabled": True,
    }
    table = "search_docs_" + uuid4().hex

    def connect():
        return connector.connect(**options)

    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute(f"""CREATE TABLE {table} (
            environment VARCHAR(32) NOT NULL, index_key VARCHAR(64) NOT NULL,
            id VARCHAR(255) NOT NULL, source_id VARCHAR(255) NOT NULL,
            `vector` MEDIUMTEXT NOT NULL, payload MEDIUMTEXT NOT NULL,
            extra MEDIUMTEXT NOT NULL, filters JSON NULL,
            source_revision BIGINT NOT NULL, version BIGINT NOT NULL,
            is_deleted TINYINT NOT NULL,
            gmt_modify TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            PRIMARY KEY (environment,index_key,id)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""")
        yield SqlIndexDocuments(
            connect, table=table, environment="test", index_key="one"
        )
    finally:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.close()
        conn.close()


def save(store, doc):
    conn = store.connect()
    cur = conn.cursor()
    try:
        assert store.save_in_transaction(cur, doc)
        conn.commit()
    finally:
        cur.close()
        conn.close()


def test_mysql_documents_survive_connections_update_replay_and_tombstones(mysql_store):
    store = mysql_store
    first = document()
    save(store, first)
    assert store.load_by_id("doc").point.vector == first.point.vector
    assert store.load_many(["doc"])["doc"].filters == first.filters
    assert store.load_by_source("source")[0].id == "doc"
    assert store.load_latest_by_id("doc").id == "doc"
    assert store.load_vector_candidates("source", "test/2")[0].id == "doc"
    assert next(iter(store.iter_batches(10)))[0].id == "doc"
    other = SqlIndexDocuments(
        store.connect, table=store.table, environment="other", index_key="one"
    )
    assert other.load_by_id("doc") is None
    save(store, replace(first, source_revision=2, is_deleted=True))
    assert store.load_by_id("doc").is_deleted
    changes = list(store.iter_change_batches_since(0, 10))
    assert changes and changes[0].upserts[0].is_deleted
    assert next(iter(store.iter_change_versions_since(0, 10)))[0].is_deleted
    assert store.load_vector_candidates("source", "test/2") == []


def test_mysql_card_initial_scan_and_equal_timestamp_paging(mysql_store):
    from search_core.providers.sql_cards import SqlCardRepository

    table = "cards_" + uuid4().hex
    conn = mysql_store.connect()
    cur = conn.cursor()
    try:
        cur.execute(f"""CREATE TABLE {table} (
            environment VARCHAR(32) NOT NULL, agent_id VARCHAR(128) NOT NULL,
            revision BIGINT NOT NULL, gmt_modify TIMESTAMP(6) NOT NULL,
            PRIMARY KEY(environment,agent_id)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin""")
        cur.execute(
            f"INSERT INTO {table} VALUES ('test','a',1,NOW(6)),('test','b',2,NOW(6)),('other','c',3,NOW(6))"
        )
        conn.commit()
        repo = SqlCardRepository(mysql_store.connect, table=table, environment="test")
        pages = list(repo.iter_changes(0, 1))
        assert [row for rows, _ in pages for row in rows] == [("a", 1), ("b", 2)]
        assert len({upper for _, upper in pages}) == 1
    finally:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.close()
        conn.close()
