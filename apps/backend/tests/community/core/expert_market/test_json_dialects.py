"""JSON discovery expressions preserve the supported database dialects."""

from sqlalchemy import column, select
from sqlalchemy.dialects import mysql, postgresql, sqlite

from agentclaw.community.core.repository.json_expression import (
    JsonText,
    SafeJsonDocument,
)


def test_postgres_safe_json_extraction_uses_installed_parser_and_native_path():
    expression = JsonText(SafeJsonDocument(column("ext")), "$.expert_origin.expert_id")
    sql = str(
        select(expression).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "ac_expert_try_jsonb" in sql
    assert "#>>" in sql
    assert "JSON_VALID" not in sql.upper()
    assert "JSON_UNQUOTE" not in sql.upper()
    assert "JSON_EXTRACT" not in sql.upper()


def test_sqlite_and_mysql_keep_native_invalid_json_guard():
    for dialect in (sqlite.dialect(), mysql.dialect()):
        sql = str(
            select(JsonText(SafeJsonDocument(column("ext")), "$[0]")).compile(
                dialect=dialect, compile_kwargs={"literal_binds": True}
            )
        )
        assert "JSON_VALID" in sql.upper()
        assert "JSON_EXTRACT" in sql.upper()


def test_postgres_bootstrap_installs_operator_migration_with_invoker_privileges(monkeypatch):
    from unittest.mock import MagicMock
    from agentclaw.community.core.expert_market.postgres_schema import (
        install_postgres_expert_json,
        POSTGRES_JSON_MIGRATION,
    )

    engine = MagicMock()
    engine.dialect.name = "postgresql"
    from agentclaw.community.core import schema
    monkeypatch.setattr(schema, "import_all_models", lambda: None)
    monkeypatch.setattr(schema, "_metadatas", lambda: [])
    schema.create_all(engine)
    statement = str(
        engine.begin.return_value.__enter__.return_value.execute.call_args.args[0]
    )
    assert statement == POSTGRES_JSON_MIGRATION.read_text()
    assert "SECURITY INVOKER" in statement
    assert "invalid_text_representation" in statement
    assert "SET search_path = pg_catalog" in statement
    assert "pg_input_is_valid" not in statement
    engine.reset_mock()
    engine.dialect.name = "sqlite"
    install_postgres_expert_json(engine)
    engine.begin.assert_not_called()
