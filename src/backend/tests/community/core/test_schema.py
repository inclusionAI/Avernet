"""Schema bootstrap — model registration and MySQL DDL adjustments.

``core/schema.py`` is what turns an empty database into the backend's schema. The
local SQLite plugin and the community plugin both run it, so a community
deployment gets the same tables a singlebox boot does — and, on MySQL, a schema
that InnoDB will actually accept.
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from agentclaw.community.core import schema


#: InnoDB's index key cap (MySQL 8, DYNAMIC row format) and utf8mb4's cost per
#: character — the budget every key has to fit inside.
MAX_KEY_BYTES = 3072
BYTES_PER_CHAR = 4


def _key_bytes(columns, prefixes: dict[str, int] | None = None) -> int:
    prefixes = prefixes or {}
    return sum(
        (prefixes.get(col.name, col.type.length) or 0) * BYTES_PER_CHAR
        for col in columns
        if isinstance(col.type, sa.String)
    )


def _oversized_keys(metadata: sa.MetaData) -> list[tuple[str, str]]:
    """Every index/unique key on ``metadata`` that MySQL would reject."""
    offenders = []
    for table in metadata.sorted_tables:
        for constraint in table.constraints:
            if isinstance(constraint, sa.UniqueConstraint):
                if _key_bytes(list(constraint.columns)) > MAX_KEY_BYTES:
                    offenders.append((table.name, str(constraint.name)))
        for index in table.indexes:
            prefixes = index.dialect_options["mysql"].get("length") or {}
            if _key_bytes(list(index.columns), prefixes) > MAX_KEY_BYTES:
                offenders.append((table.name, str(index.name)))
    return offenders


class TestPrepareForMysql:
    def test_leaves_a_key_that_already_fits_untouched(self):
        metadata = sa.MetaData()
        table = sa.Table(
            "fits",
            metadata,
            sa.Column("a", sa.String(64)),
            sa.Column("b", sa.String(64)),
            sa.Index("idx_fits", "a", "b"),
        )

        assert schema.prepare_for_mysql(metadata) == []
        index = next(iter(table.indexes))
        assert not index.dialect_options["mysql"].get("length")

    def test_prefixes_an_oversized_index(self):
        metadata = sa.MetaData()
        table = sa.Table(
            "wide",
            metadata,
            sa.Column("big", sa.String(2048)),
            sa.Index("idx_wide", "big"),
        )

        adjusted = schema.prepare_for_mysql(metadata)

        assert adjusted, "an 8192-byte key must be adjusted"
        index = next(iter(table.indexes))
        prefixes = index.dialect_options["mysql"]["length"]
        assert prefixes["big"] < 2048
        assert _key_bytes(list(index.columns), prefixes) <= MAX_KEY_BYTES

    def test_cuts_the_widest_column_and_leaves_narrow_ones_whole(self):
        metadata = sa.MetaData()
        table = sa.Table(
            "mixed",
            metadata,
            sa.Column("wide", sa.String(1024)),
            sa.Column("narrow", sa.String(20)),
            sa.Index("idx_mixed", "wide", "narrow"),
        )

        schema.prepare_for_mysql(metadata)

        prefixes = next(iter(table.indexes)).dialect_options["mysql"]["length"]
        assert "narrow" not in prefixes
        assert prefixes["wide"] < 1024

    def test_converts_an_oversized_unique_constraint_to_a_prefixed_index(self):
        # SQLAlchemy's UniqueConstraint cannot carry prefix lengths; a unique
        # Index can, and MySQL treats the two as the same object.
        metadata = sa.MetaData()
        table = sa.Table(
            "uniq",
            metadata,
            sa.Column("a", sa.String(1024)),
            sa.Column("b", sa.String(1024)),
            sa.UniqueConstraint("a", "b", name="uk_uniq_a_b"),
        )

        schema.prepare_for_mysql(metadata)

        assert not [
            c for c in table.constraints if isinstance(c, sa.UniqueConstraint)
        ], "the oversized UniqueConstraint should have been replaced"
        index = next(i for i in table.indexes if i.name == "uk_uniq_a_b")
        assert index.unique is True
        assert _key_bytes(list(index.columns), index.dialect_options["mysql"]["length"]) <= MAX_KEY_BYTES

    def test_is_idempotent(self):
        metadata = sa.MetaData()
        sa.Table(
            "again",
            metadata,
            sa.Column("big", sa.String(2048)),
            sa.UniqueConstraint("big", name="uk_again_big"),
        )

        schema.prepare_for_mysql(metadata)
        assert schema.prepare_for_mysql(metadata) == []
        assert _oversized_keys(metadata) == []

    def test_emits_valid_mysql_ddl(self):
        metadata = sa.MetaData()
        table = sa.Table(
            "ddl",
            metadata,
            sa.Column("entity_id", sa.String(1024)),
            sa.Column("env", sa.String(20)),
            sa.UniqueConstraint("entity_id", "env", name="uk_ddl_scope"),
        )
        schema.prepare_for_mysql(metadata)
        dialect = mysql.dialect()

        CreateTable(table).compile(dialect=dialect)
        index = next(iter(table.indexes))
        statement = str(CreateIndex(index).compile(dialect=dialect))

        assert "CREATE UNIQUE INDEX" in statement
        assert "entity_id(" in statement, "the wide column should carry a prefix"
        assert "env(" not in statement, "the narrow column should stay whole"


class TestRealSchema:
    def test_every_shipped_key_fits_innodb_after_preparation(self):
        """The real metadata must be MySQL-deployable, not just the synthetic cases.

        These models were written for a store whose DDL is applied out of band, so
        a number of keys are far past InnoDB's cap as declared. Guarding the
        prepared result here is what makes ``database.backend: mysql`` with
        ``create_schema: true`` a supported configuration.
        """
        schema.import_all_models()
        metadatas = schema._metadatas()

        assert _oversized_keys(metadatas[0]), (
            "expected the shipped models to declare keys past InnoDB's cap — "
            "if this is no longer true the preparation step may be unnecessary"
        )

        for metadata in metadatas:
            schema.prepare_for_mysql(metadata)
            assert _oversized_keys(metadata) == []

    def test_real_schema_compiles_as_mysql_ddl(self):
        schema.import_all_models()
        dialect = mysql.dialect()

        for metadata in schema._metadatas():
            schema.prepare_for_mysql(metadata)
            for table in metadata.sorted_tables:
                CreateTable(table).compile(dialect=dialect)
                for index in table.indexes:
                    CreateIndex(index).compile(dialect=dialect)

    def test_create_all_builds_the_full_schema_on_a_fresh_database(self, tmp_path):
        engine = sa.create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")

        schema.create_all(engine)

        tables = sa.inspect(engine).get_table_names()
        # The exact count moves with the models; what matters is that the
        # bootstrap is comprehensive rather than whatever the import graph
        # happened to pull in, and that the core tables are present.
        assert len(tables) > 50
        assert "ac_bots" in tables
        assert "aw_langfuse_traces" in tables, "the private bot_chat Base must be emitted too"
