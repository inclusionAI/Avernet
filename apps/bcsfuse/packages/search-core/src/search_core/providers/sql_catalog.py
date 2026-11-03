"""Public Card source and atomic Doc publication over injected DB-API connections."""

import sqlite3

from search_core.providers.card_schema import initialize_sqlite_cards
from search_core.providers.sql_card_preparation import CardPreparation
from search_core.providers.sql_cards import SqlCardRepository as CardStorage
from search_core.providers.sql_index_builds import SQLITE_SCHEMA
from search_core.providers.sql_index_documents import (
    initialize_sqlite_documents,
    validate_environment,
)


class SqlCatalogRepository(CardPreparation, CardStorage):
    pass


def sqlite_catalog_repository(path: str, *, environment: str = "local"):
    if not path or path == ":memory:":
        raise ValueError("catalog requires a SQLite file")
    validate_environment(environment)

    def connect():
        return sqlite3.connect(path, timeout=10)

    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        initialize_sqlite_cards(connection)
        initialize_sqlite_documents(connection)
        connection.execute(SQLITE_SCHEMA)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return SqlCatalogRepository(
        connect, table="bcsfuse_agent_cards", environment=environment, placeholder="?"
    )
