"""Public Card source and atomic Doc publication over injected DB-API connections."""

import sqlite3

from search_core.providers.card_schema import (
    TABLE,
    initialize_sqlite_cards,
    validate_card_table,
)
from search_core.providers.sql_card_preparation import CardPreparation
from search_core.providers.sql_cards import SqlCardRepository as CardStorage
from search_core.providers.sql_index_builds import SQLITE_SCHEMA
from search_core.providers.sql_index_documents import (
    TABLE as DOCUMENT_TABLE,
)
from search_core.providers.sql_index_documents import (
    initialize_sqlite_documents,
    validate_environment,
)


class SqlCatalogRepository(CardPreparation, CardStorage):
    def __init__(self, *args, document_table=DOCUMENT_TABLE, **kwargs):
        self.document_table = validate_card_table(document_table)
        super().__init__(*args, **kwargs)


def sqlite_catalog_repository(
    path: str,
    *,
    environment: str = "local",
    table: str = TABLE,
    document_table: str = DOCUMENT_TABLE,
):
    if not path or path == ":memory:":
        raise ValueError("catalog requires a SQLite file")
    validate_environment(environment)
    validate_card_table(table)
    validate_card_table(document_table)

    def connect():
        return sqlite3.connect(path, timeout=10)

    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        initialize_sqlite_cards(connection, table=table)
        initialize_sqlite_documents(connection, table=document_table)
        connection.execute(SQLITE_SCHEMA)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return SqlCatalogRepository(
        connect,
        table=table,
        document_table=document_table,
        environment=environment,
        placeholder="?",
    )
