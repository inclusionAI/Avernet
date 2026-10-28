"""Install the same PostgreSQL helper used by operator-managed migrations."""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine


POSTGRES_JSON_MIGRATION = (
    Path(__file__).with_name("sql") / "2026_10_27_expert_json_postgresql.sql"
)


def install_postgres_expert_json(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text(POSTGRES_JSON_MIGRATION.read_text(encoding="utf-8")))
