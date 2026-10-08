"""Contract checks on the column-type choices in this module's DDL.

Both rules asserted here were violated once, in the first draft of the
OceanBase alignment change, and neither violation was caught by anything in CI —
they were found by review. The prose in each file explains the reasoning; these
tests are what make a future "tidy this up for consistency" edit fail loudly
instead of silently corrupting an audit column.

WHY TEXT RATHER THAN A LIVE SCHEMA. The suite runs on SQLite, which has no
TIMESTAMP/DATETIME distinction, no implicit-default rule, and does not parse the
OceanBase modifiers these files carry. The distinction being guarded therefore
cannot be observed by creating the tables — only by reading what is declared.
The sibling ``core/task_queue/test_task_queue_schema_contract.py`` guards its
file the same way and for the same reason.

WHY ``bot_startup_script/sql`` IS IN SCOPE HERE. The rule these tests state is
about a column-type choice under OceanBase, not about one module, and
``ac_bot_startup_script`` is materialised by this module's ``script`` category
-- it is the manifest's own storage for that construct, kept in its own package
only because #926 shipped it first. It sat outside this directory and therefore
outside this rule, and that is exactly where the drift turned up: the deployed
table carries TIMESTAMP audit columns while the file declared DATETIME, found by
reading a ``SHOW CREATE TABLE`` rather than by anything in CI. Scoping by rule
instead of by directory is what closes that.
"""

from pathlib import Path
import re


_CORE = Path(__file__).parents[4] / "src" / "agentclaw" / "community" / "core"

_SQL_DIR = _CORE / "bot_config_manifest" / "sql"

#: Every directory whose DDL this rule governs. ``_SQL_DIR`` stays a single
#: directory because ``_APPLICATION_SUPPLIED`` addresses files inside it by
#: name.
_SQL_DIRS = (_SQL_DIR, _CORE / "bot_startup_script" / "sql")

#: Columns filled by the application, which must stay DATETIME. TIMESTAMP reads
#: the bound naive value as session-local and converts it, so an instant the
#: application already normalised to UTC (``fetched_at``) is stored shifted by
#: the session offset.
#:
#: The apply table's started_at/finished_at used to be listed here and are
#: not any more: the DDL review standard refused them as DATETIME (see
#: ``_DDL_STANDARD_FORBIDDEN_TYPES``), and their round trip through one
#: session is the identity, so TIMESTAMP loses nothing the application reads.
_APPLICATION_SUPPLIED = {
    "2026_08_31_manifest_content.sql": ("fetched_at",),
}

#: The DDL review standard (研发规范) that gates provisioning on OceanBase.
#: ac_bot_config_manifest_apply was refused on exactly these two rules -- a
#: column named `trigger`, and DATETIME started_at/finished_at -- which is why
#: the record table never existed in production while its lock table did.
_DDL_STANDARD_FORBIDDEN_TYPES = {"bit", "float", "double", "datetime", "enum", "set"}
_DDL_STANDARD_FILES = (
    "2026_08_31_bot_config_manifest_apply.sql",
    "2026_09_07_repair_misprovisioned_bot_config_manifest_apply.sql",
)

#: Filled by the database itself, so TIMESTAMP's conversion is a no-op round
#: trip and matches ac_bots. Every table here has both.
_DB_FILLED = ("gmt_create", "gmt_modified")


def _tables(path: Path) -> dict[str, dict[str, str]]:
    """Map table name -> {column name: rest of its declaration}.

    SCOPED PER TABLE, NOT PER FILE, and that is the whole point. An earlier
    version of this helper returned one dict per file; a file declaring two
    tables that share a column name — which
    2026_08_31_bot_config_manifest_apply.sql does, with the apply table and its
    lock both carrying gmt_create/gmt_modified — kept only the last match, so
    the apply table's audit columns went unchecked by every test here. That is
    the same table the implicit-ON-UPDATE hazard lived in, so the hole sat
    exactly where the coverage was supposed to be.

    Comments are stripped first: ``DEFAULT CURRENT_TIMESTAMP`` appears
    throughout the prose in these files, and the type token is only meaningful
    in the position directly after the backticked column name.
    """
    body = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )
    starts = [
        (match.group(1), match.start(), match.group(0).startswith("ALTER"))
        for match in re.finditer(
            r"(?:CREATE TABLE\s+(?:IF NOT EXISTS\s+)?|ALTER TABLE\s+)`(\w+)`", body
        )
    ]
    assert starts, f"{path.name}: no CREATE TABLE or ALTER TABLE found"

    bounds = [start for _, start, _ in starts] + [len(body)]
    tables: dict[str, dict[str, str]] = {}
    for index, (name, _, is_alter) in enumerate(starts):
        segment = body[bounds[index]:bounds[index + 1]]
        # An ALTER's columns are introduced by ADD COLUMN; a CREATE's are the
        # bare backticked declarations. Both are checked, and that is the
        # point of handling ALTER at all: a migration adding a gmt_ column
        # would otherwise slip past every rule in this file — precisely the
        # kind of "consistency tidy-up" these tests exist to catch.
        pattern = (
            r"ADD COLUMN\s+`(\w+)`\s+(.+?),?$"
            if is_alter
            else r"^\s*`(\w+)`\s+(.+?),?$"
        )
        found = {
            column: rest.strip()
            for column, rest in re.findall(pattern, segment, flags=re.M)
        }
        tables.setdefault(name, {}).update(found)
    return tables


def _created_tables(path: Path) -> set[str]:
    """The tables this file *creates*, as opposed to alters.

    The audit-column rules split on exactly this. "Every table declares
    gmt_create and gmt_modified" is a statement about a table's creation — an
    ALTER adding one unrelated column cannot satisfy it and must not be asked
    to. The *type* rules do apply to both: a migration that added a gmt_
    column as a bare DATETIME, or as a TIMESTAMP that picks up MySQL's
    implicit ON UPDATE, is the same corruption arriving by a different door.
    """
    body = path.read_text(encoding="utf-8")
    return {
        match.group(1)
        for match in re.finditer(r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?`(\w+)`", body)
    }


def _declarations(path: Path) -> dict[str, str]:
    """Every column in the file as ``table.column`` -> declaration.

    Flat, but with table-qualified keys, so same-named columns in sibling
    tables can no longer shadow each other.
    """
    return {
        f"{table}.{column}": rest
        for table, columns in _tables(path).items()
        for column, rest in columns.items()
    }


def _sql_files() -> list[Path]:
    files = sorted(path for directory in _SQL_DIRS for path in directory.glob("*.sql"))
    assert files, f"no DDL found under {[str(d) for d in _SQL_DIRS]}"
    # Asserted per directory as well as overall: a mistyped path would other-
    # wise leave one directory silently contributing nothing, which is the
    # shape of the shadowing bug ``_tables`` already documents.
    for directory in _SQL_DIRS:
        assert any(
            path.parent == directory for path in files
        ), f"no DDL found under {directory}"
    return files


def test_application_supplied_times_are_datetime() -> None:
    """The regression fixed in acb6c1c5: these must not become TIMESTAMP."""
    for filename, columns in _APPLICATION_SUPPLIED.items():
        tables = _tables(_SQL_DIR / filename)
        for column in columns:
            found = {
                table: declarations[column]
                for table, declarations in tables.items()
                if column in declarations
            }
            assert found, f"{filename}: `{column}` is gone from every table"
            for table, declaration in found.items():
                declared_type = declaration.split()[0].lower()
                assert declared_type == "datetime", (
                    f"{table}.{column} is declared {declared_type}, not datetime. "
                    "It is filled by the application, so TIMESTAMP would "
                    "reinterpret the bound value as session-local and store it "
                    "shifted."
                )


def test_database_filled_times_are_timestamp() -> None:
    """The other half of the rule, so the split is asserted in both directions.

    Every table is checked independently — including both tables in the apply
    file, which is what the per-file version of this silently skipped.
    """
    for path in _sql_files():
        created = _created_tables(path)
        for table, declarations in _tables(path).items():
            for column in _DB_FILLED:
                if column not in declarations:
                    # A migration file touching other columns says nothing
                    # about these; the file that created the table did.
                    assert table not in created, f"{table}: {column} is missing"
                    continue
                declared_type = declarations[column].split()[0].lower()
                assert declared_type == "timestamp", (
                    f"{table}.{column} is declared {declared_type}, not timestamp"
                )


def test_every_table_in_every_file_is_parsed() -> None:
    """Guards the parser itself, not the DDL.

    Both tests above are only as complete as ``_tables`` is. If it silently
    returned one table for a two-table file again, they would go quiet rather
    than fail, which is precisely how the shadowing bug survived review round 2.
    """
    tables = {
        table
        for path in _sql_files()
        for table in _tables(path)
    }
    assert tables == {
        "ac_bot_config_manifest",
        "ac_bot_config_manifest_apply",
        "ac_bot_config_manifest_apply_lock",
        "ac_manifest_content",
        "ac_source_credential",
        "ac_bot_cli_tool",
        "ac_bot_startup_script",
    }


def test_apply_tables_pass_the_ddl_review_standard() -> None:
    """The two findings the standard raised, held so they cannot come back.

    Scoped to the apply file: ac_manifest_content.fetched_at is still DATETIME
    and is a separate change with its own UTC-normalisation reasoning.
    """
    for filename in _DDL_STANDARD_FILES:
        tables = _tables(_SQL_DIR / filename)
        assert tables, f"{filename}: nothing parsed"
        for table, declarations in tables.items():
            assert "trigger" not in declarations, (
                f"{table}: `trigger` is a SQL keyword; the column is apply_trigger"
            )
            for column, rest in declarations.items():
                declared_type = rest.split()[0].lower().split("(")[0]
                assert declared_type not in _DDL_STANDARD_FORBIDDEN_TYPES, (
                    f"{table}.{column} is declared {declared_type}, which the DDL "
                    "review standard refuses"
                )
        apply_columns = tables["ac_bot_config_manifest_apply"]
        assert "apply_trigger" in apply_columns
        for column in ("started_at", "finished_at"):
            assert apply_columns[column].split()[0].lower() == "timestamp"


def test_no_bare_timestamp_column_can_attach_an_implicit_on_update() -> None:
    """Guards the blocking finding from round 1 of review.

    Under ``explicit_defaults_for_timestamp=OFF`` the server attaches
    ``DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`` to a table's first
    TIMESTAMP column that is NOT NULL and declares no DEFAULT — and ON UPDATE
    fires on any change to the row, not only to that column. A ``started_at``
    caught that way is overwritten with the finish time on every apply.
    """
    for path in _sql_files():
        for column, rest in _declarations(path).items():  # table-qualified keys
            tokens = rest.lower()
            if tokens.split()[0] != "timestamp":
                continue
            # An explicit NULL exempts the column; NOT NULL does not. Matching a
            # bare "null" substring would treat "NOT NULL" as the exemption and
            # pass exactly the declaration this test exists to reject.
            explicitly_nullable = re.search(r"(?<!not )\bnull\b", tokens) is not None
            assert "default" in tokens or explicitly_nullable, (
                f"{path.name}: {column} is a bare TIMESTAMP NOT NULL with no "
                "DEFAULT; the server may attach ON UPDATE CURRENT_TIMESTAMP to it"
            )


def test_parser_preserves_create_if_not_exists_and_alter_columns(tmp_path: Path) -> None:
    path = tmp_path / "mixed.sql"
    path.write_text(
        "CREATE TABLE IF NOT EXISTS `created` (\n"
        "  `gmt_create` TIMESTAMP DEFAULT CURRENT_TIMESTAMP\n"
        ");\n"
        "ALTER TABLE `created` ADD COLUMN `extra` TEXT;\n"
        "ALTER TABLE `existing` ADD COLUMN `gmt_modified` DATETIME;\n",
        encoding="utf-8",
    )
    tables = _tables(path)
    assert set(tables) == {"created", "existing"}, f"parsed tables: {tables}"
    assert set(tables["created"]) == {"gmt_create", "extra"}, tables
    assert tables["existing"]["gmt_modified"].startswith("DATETIME"), tables
    assert _created_tables(path) == {"created"}, "CREATE IF NOT EXISTS must count as creation"
