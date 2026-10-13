"""Schema-migration contract for the `ac_agent_swarm_*` tables (task 12.3).

Forward-only doctrine (D38). Once the swarm is provisioned and carrying
durable state, the resumable-slice loop (D34) and the reclaim breaker (D40)
resume from those rows, so every change to `core/agentswarm/sql/*.sql` must
be **additive and backward-readable**:

* Files carry a `YYYY_MM_DD_` prefix and apply **ascending by file name**,
  with the earliest date first, **before** the application release that uses
  the new shape.
* A migration adds columns/indexes/tables (`ALTER ... ADD COLUMN`, `ADD
  INDEX`, `CREATE TABLE`) that old code ignores; it never drops or redefines
  what old code reads (`DROP TABLE|COLUMN|INDEX`, `TRUNCATE`, `MODIFY`,
  `CHANGE` are out — physical reversal is re-forward-additive, not subtractive).
* Old application code must keep running against post-migration rows. A
  stripped/stopped fleet with a half-rolled schema is the accepted failure
  mode being guarded against — a dropped column is unrecoverable, while an
  ignored extra column merely costs bytes until the next release drains it.
* Rollback is: deploy the previous application image. Never DROP back.

The ORM (`repository/models.py`) is the source of truth and the DDL files are
derived provisioning, exactly as `test_task_queue_schema_contract.py`
establishes for `core/task_queue`. The drift worth guarding is any direction
of divergence: a column or unique index the ORM declares but the DDL lacks
means a freshly provisioned OceanBase database rejects every write; extra DDL
columns mean provisioned tables accumulate blobs no code ever reads. Unique
indexes are compared **by name and ordered column list** — a different column
order is a different constraint, and the (env, tenant) dedup scope is exactly
what these indexes encode (D25/D29).

The DDL is read as text, never executed: the OceanBase-only modifiers
(`GLOBAL`, `LOCAL`, `BLOCK_SIZE`) are not parseable by the SQLite engine the
suite runs on, which is the same reason they cannot be expressed in the ORM
and live in the file at all.

Wave-B coordination: the audit table ships in the same wave
(`2026_10_08_agent_swarm.sql`, another agent's file). This suite
discovers `*.sql` generically and iterates `ALL_MODELS` dynamically, so the
audit table joins parity checks the moment both its model and its DDL land.
"""
from __future__ import annotations

import re
from pathlib import Path

from agentclaw.community.core.agentswarm.repository import registry as om

_BACKEND_ROOT = Path(__file__).parents[5]
_SQL_DIR = (
    _BACKEND_ROOT
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "agentswarm"
    / "sql"
)

_DATE_PREFIX = re.compile(r"^\d{4}_\d{2}_\d{2}_")
# Destructive/shape-changing statements are forbidden in executable SQL
# (comment-stripped text): each is unrecoverable for a running fleet or
# silently changes what old code reads back (D38).
_FORBIDDEN_STATEMENTS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern)
    for pattern in (
        r"\bDROP\s+TABLE\b",
        r"\bDROP\s+DATABASE\b",
        r"\bDROP\s+INDEX\b",
        r"\bDROP\s+KEY\b",
        r"\bDROP\s+PRIMARY\s+KEY\b",
        r"\bDROP\s+COLUMN\b",
        r"\bDROP\s+FOREIGN\s+KEY\b",
        r"\bTRUNCATE\b",
        r"\bRENAME\s+TABLE\b",
    )
)
# Even commented-out example migrations are doctrine-bearing guidance for
# operators, so ALTER shape is checked on raw text (task_queue exemplar:
# the commented `ALTER TABLE ... ADD COLUMN` block in
# 2026_09_06_task_queue.sql). An ALTER may only ADD.
_ALTER_TABLE = re.compile(r"\bALTER\s+TABLE\b", re.IGNORECASE)
_NON_ADDITIVE_CLAUSE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bDROP\b",
        r"\bMODIFY\b",
        r"\bCHANGE\s+COLUMN\b",
        r"\bALTER\s+COLUMN\b",
        r"\bRENAME\b",
    )
)

_MANDATORY_COLUMNS = ("env", "tenant", "is_deleted", "gmt_create", "gmt_modified")

# Wave-B pending exemptions (task 12.3 coordination note). A table listed
# here is temporarily tolerated as ORM-model-without-DDL (or the reverse)
# ONLY while its sibling artifact is landing in this same wave; parity then
# catches it. Every entry needs a dated TODO, a clear reason, and removal
# when the paired file lands — an empty dict means parity holds right now.
_PENDING_EXEMPT_TABLES: dict[str, str] = {
    # "ac_agent_swarm_audit": (
    #     "TODO(2026-10-09, wave-B): audit model shipped in ALL_MODELS before "
    #     "its 2026_10_09 sql landed; remove this entry once B3's DDL file "
    #     "merges (parity then enforces both directions)."
    # ),
}

# The one OceanBase modifier that is load-bearing for this schema today:
# the job queue dedups one job per (env, tenant, run_id) and is the claim
# source — a partition-LOCAL unique key would allow the same run_id once
# per partition and double-claim slices, exactly the task_queue rationale
# (its exemplar file documents GLOBAL as load-bearing). Other unique
# indexes carry LOCAL or no modifier in the current DDL and are not
# required to gain one.
_REQUIRED_UNIQUE_MODIFIERS: dict[str, dict[str, str]] = {
    "ac_agent_swarm_job": {"uk_job_run": "GLOBAL"},
}


def _sql_files() -> list[Path]:
    return sorted(_SQL_DIR.glob("*.sql"))


def _strip_comments(text: str) -> str:
    """Remove `--` comment bodies so doctrine prose never trips a SQL parse."""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("--")
    )


def _parse_tables(text: str) -> dict[str, str]:
    """Map table name -> CREATE TABLE body (column + index block)."""
    bodies = re.findall(
        r"CREATE TABLE IF NOT EXISTS `(\w+)`\s*\((.*?)^\)", text,
        flags=re.DOTALL | re.MULTILINE,
    )
    return {name: body for name, body in bodies}


def _parse_columns(body: str) -> set[str]:
    """Column names declared by a CREATE TABLE body, excluding index clauses.

    Index clauses (`KEY`, `UNIQUE KEY`, `PRIMARY KEY`) also begin with a
    backticked token at the head of a line, but only a column def is followed
    by a type token rather than a parenthesised column list.
    """
    return {
        name
        for name, following in re.findall(
            r"^\s*`([^`]+)`\s+(\S+)", body, flags=re.MULTILINE
        )
        if not following.startswith("(")
    }


def _parse_unique_indexes(body: str) -> dict[str, tuple[str, ...]]:
    found: dict[str, tuple[str, ...]] = {}
    for name, columns in re.findall(
        r"UNIQUE KEY\s+`([^`]+)`\s*\(([^)]*)\)", body, flags=re.DOTALL
    ):
        found[name] = tuple(re.findall(r"`([^`]+)`", columns))
    return found


def _parse_unique_modifiers(body: str) -> dict[str, str | None]:
    modifiers: dict[str, str | None] = {}
    for name, modifier in re.findall(
        r"UNIQUE KEY\s+`([^`]+)`\s*\([^)]*\)\s*(?:BLOCK_SIZE\s+\d+\s+)?(LOCAL|GLOBAL)?",
        body,
        flags=re.DOTALL,
    ):
        modifiers[name] = modifier or None
    return modifiers


def _ddl_by_table() -> dict[str, str]:
    tables: dict[str, str] = {}
    for file in _sql_files():
        tables.update(_parse_tables(_strip_comments(file.read_text(encoding="utf-8"))))
    return tables


def _orm_tables() -> dict[str, type]:
    return {model.__tablename__: model for model in om.ALL_MODELS}


def _orm_columns(model: type) -> set[str]:
    return {column.name for column in model.__table__.columns}


def _orm_unique_indexes(model: type) -> dict[str, tuple[str, ...]]:
    return {
        item.name: tuple(column.name for column in item.columns)
        for item in model.__table__.indexes
        if item.unique
    }


def _known_exemptions() -> list[str]:
    known = _orm_tables().keys() | _ddl_by_table().keys()
    return [name for name in _PENDING_EXEMPT_TABLES if name in known]


class TestMigrationPolicy:
    """Per-file policy: dated names, forward-only statements, additive ALTER."""

    def test_sql_files_exist_and_are_date_prefixed(self):
        files = _sql_files()
        assert files, f"no *.sql under {_SQL_DIR}"
        for file in files:
            assert _DATE_PREFIX.match(file.name), (
                f"{file.name}: migration files must carry a YYYY_MM_DD_ prefix "
                "(apply order is filename-sorted, earliest first)"
            )
        # Sorted file names must give a monotonically non-decreasing date
        # ordering — the operator's apply order. The fixed-width prefix makes
        # lexicographic sort equal date sort, so this is a stability claim on
        # the names, not a tautology: a misplaced date breaks it.
        prefixes = [file.name[:11] for file in files]
        assert prefixes == sorted(prefixes), (
            "migration file names do not sort by date; operators apply "
            "ascending by file name"
        )
        assert len(set(prefixes)) == len(prefixes), (
            "two migration files share one YYYY_MM_DD prefix"
        )

    def test_no_destructive_statements(self):
        """No DROP/TRUNCATE/RENAME anywhere in executable SQL (D38).

        Rollback is re-deploying the previous image against post-migration
        rows; dropping back is unrecoverable for resumable-slice state.
        Doctrine prose in comments is exempt (comments are stripped).
        """
        for file in _sql_files():
            body = _strip_comments(file.read_text(encoding="utf-8"))
            for pattern in _FORBIDDEN_STATEMENTS:
                match = pattern.search(body, re.IGNORECASE)
                assert match is None, (
                    f"{file.name}: destructive/policy-forbidden statement "
                    f"{match!r}; rollback is additive-only, re-deploy the "
                    "previous image (D38)"
                )

    def test_alter_statements_are_additive_only(self):
        """Every ALTER TABLE — including documented ones — may only ADD.

        Mirrors the task_queue exemplar's commented `ALTER ... ADD COLUMN`
        block: even example text guides operators, so a documented
        MODIFY/CHANGE/DROP ALTER is policy drift too.
        """
        for file in _sql_files():
            text = file.read_text(encoding="utf-8")
            for match in _ALTER_TABLE.finditer(text):
                window = text[match.start(): match.start() + 800]
                for clause in _NON_ADDITIVE_CLAUSE:
                    bad = clause.search(window)
                    assert bad is None, (
                        f"{file.name}: ALTER TABLE statement contains "
                        f"non-additive clause {bad!r}; reversals must be "
                        "additive (add a new column/row shape, never rewrite "
                        "the old one)"
                    )

    def test_every_create_table_has_mandatory_columns_and_id_pk(self):
        ddl = _ddl_by_table()
        assert ddl, "no CREATE TABLE found in any agentswarm sql file"
        for table, body in ddl.items():
            columns = _parse_columns(body)
            for required in _MANDATORY_COLUMNS:
                assert required in columns, (
                    f"{table}: mandatory column `{required}` missing from DDL"
                )
            assert re.search(r"PRIMARY KEY \(`id`\)", body), (
                f"{table}: PRIMARY KEY (`id`) missing from DDL"
            )

    def test_unique_indexes_are_env_tenant_scoped(self):
        """Dedup scope is (env, tenant, ...) in the DDL, as in the ORM (D25)."""
        for table, body in _ddl_by_table().items():
            for name, columns in _parse_unique_indexes(body).items():
                assert columns[:2] == ("env", "tenant"), (
                    f"{table}.{name}: unique key is not (env, tenant)-scoped "
                    f"(columns={columns})"
                )

    def test_load_bearing_unique_modifiers_are_pinned(self):
        """GLOBAL stays on exactly the indexes whose dedup spans partitions.

        A partition-LOCAL unique key allows one entry per partition, which
        silently defeats queue dedup; `uk_job_run` is this schema's claim
        source. No index is required to gain a modifier it does not have
        today — the rest are LOCAL or bare by current provisioning choice.
        """
        for table, body in _ddl_by_table().items():
            expected = _REQUIRED_UNIQUE_MODIFIERS.get(table, {})
            modifiers = _parse_unique_modifiers(body)
            for name, modifier in modifiers.items():
                assert modifier in (None, "LOCAL", "GLOBAL"), (
                    f"{table}.{name}: unknown partition modifier {modifier!r}"
                )
                if name in expected:
                    assert modifier == expected[name], (
                        f"{table}.{name}: partition modifier must be "
                        f"{expected[name]!r}, found {modifier!r}"
                    )


class TestOrmDdlParity:
    """Model↔DDL parity in both directions: any drift is a failed deploy."""

    def test_every_orm_table_has_a_create_table(self):
        ddl = _ddl_by_table()
        for name in _orm_tables():
            if name in _PENDING_EXEMPT_TABLES:
                continue
            assert name in ddl, (
                f"ORM model {name} has no CREATE TABLE in any sql file "
                "(a fresh OceanBase provision would reject every write)"
            )

    def test_every_create_table_has_an_orm_model(self):
        orm = _orm_tables()
        for name in _ddl_by_table():
            if name in _PENDING_EXEMPT_TABLES:
                continue
            assert name in orm, (
                f"DDL table {name} has no ORM model (provisioned table would "
                "carry columns no code reads)"
            )

    def test_ddl_orm_column_parity_both_directions(self):
        """Same column set in the ORM and the DDL, per table.

        ORM→DDL keeps fresh provisions insertable; DDL→ORM keeps provisioned
        tables honest — an extra DDL column is dead weight, a missing ORM
        column means the file drifted and was never regenerated.
        """
        ddl = _ddl_by_table()
        for name, model in _orm_tables().items():
            if name in _PENDING_EXEMPT_TABLES:
                continue
            orm_cols = _orm_columns(model)
            ddl_cols = _parse_columns(ddl[name]) | {"id", "gmt_create", "gmt_modified"}
            missing_in_ddl = orm_cols - ddl_cols
            extra_in_ddl = ddl_cols - orm_cols
            assert not missing_in_ddl, (
                f"{name}: ORM columns absent from DDL: {sorted(missing_in_ddl)}"
            )
            assert not extra_in_ddl, (
                f"{name}: DDL columns no ORM model declares: {sorted(extra_in_ddl)}"
            )

    def test_ddl_orm_unique_index_parity_name_and_order(self):
        """Every DDL unique index matches an ORM Index by name + ordered
        columns, and vice versa. Column order is part of the constraint:
        a differently-ordered unique index is a different dedup scope.
        """
        ddl = _ddl_by_table()
        for name, model in _orm_tables().items():
            if name in _PENDING_EXEMPT_TABLES:
                continue
            orm_unique = _orm_unique_indexes(model)
            ddl_unique = _parse_unique_indexes(ddl[name])
            assert ddl_unique == orm_unique, (
                f"{name}: unique-index drift between DDL and ORM "
                f"(ddl={ddl_unique}, orm={orm_unique})"
            )

    def test_pending_exemptions_are_not_stale(self):
        """Exemption hygiene: entries point at real in-flight drift only.

        An exemption whose table exists in neither the ORM nor the DDL is a
        stale entry masking nothing — remove it. With the dict empty this is
        the all-green assertion that parity is currently enforced everywhere.
        """
        stale = sorted(set(_PENDING_EXEMPT_TABLES) - set(_known_exemptions()))
        assert not stale, f"stale exemptions referencing unknown tables: {stale}"
