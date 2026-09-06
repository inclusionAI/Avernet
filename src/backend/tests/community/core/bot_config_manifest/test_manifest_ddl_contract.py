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
"""

from pathlib import Path
import re


_SQL_DIR = (
    Path(__file__).parents[4]
    / "src"
    / "agentclaw"
    / "community"
    / "core"
    / "bot_config_manifest"
    / "sql"
)

#: Columns filled by the application, which must stay DATETIME. TIMESTAMP reads
#: the bound naive value as session-local and converts it, so an instant the
#: application already normalised to UTC (``fetched_at``) or took from
#: ``datetime.now()`` (the apply times) is stored shifted by the session offset.
_APPLICATION_SUPPLIED = {
    "2026_08_31_bot_config_manifest_apply.sql": ("started_at", "finished_at"),
    "2026_08_31_manifest_content.sql": ("fetched_at",),
}

#: Filled by the database itself, so TIMESTAMP's conversion is a no-op round
#: trip and matches ac_bots. Every table here has both.
_DB_FILLED = ("gmt_create", "gmt_modified")


def _declarations(path: Path) -> dict[str, str]:
    """Map column name -> the rest of its declaration, comments stripped.

    Comments have to go first: ``DEFAULT CURRENT_TIMESTAMP`` appears throughout
    the prose in these files, and the type token is only meaningful in the
    position directly after the backticked column name.
    """
    body = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("--")
    )
    return {
        name: rest.strip()
        for name, rest in re.findall(r"^\s*`(\w+)`\s+(.+?),?$", body, flags=re.M)
    }


def _sql_files() -> list[Path]:
    files = sorted(_SQL_DIR.glob("*.sql"))
    assert files, f"no DDL found under {_SQL_DIR}"
    return files


def test_application_supplied_times_are_datetime() -> None:
    """The regression fixed in acb6c1c5: these must not become TIMESTAMP."""
    for filename, columns in _APPLICATION_SUPPLIED.items():
        declarations = _declarations(_SQL_DIR / filename)
        for column in columns:
            assert column in declarations, f"{filename}: {column} is gone"
            declared_type = declarations[column].split()[0].lower()
            assert declared_type == "datetime", (
                f"{filename}: `{column}` is declared {declared_type}, not datetime. "
                "It is filled by the application, so TIMESTAMP would reinterpret "
                "the bound value as session-local and store it shifted."
            )


def test_database_filled_times_are_timestamp() -> None:
    """The other half of the rule, so the split is asserted in both directions."""
    for path in _sql_files():
        declarations = _declarations(path)
        for column in _DB_FILLED:
            assert column in declarations, f"{path.name}: {column} is missing"
            declared_type = declarations[column].split()[0].lower()
            assert declared_type == "timestamp", (
                f"{path.name}: `{column}` is declared {declared_type}, not timestamp"
            )


def test_no_bare_timestamp_column_can_attach_an_implicit_on_update() -> None:
    """Guards the blocking finding from round 1 of review.

    Under ``explicit_defaults_for_timestamp=OFF`` the server attaches
    ``DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP`` to a table's first
    TIMESTAMP column that is NOT NULL and declares no DEFAULT — and ON UPDATE
    fires on any change to the row, not only to that column. A ``started_at``
    caught that way is overwritten with the finish time on every apply.
    """
    for path in _sql_files():
        for column, rest in _declarations(path).items():
            tokens = rest.lower()
            if tokens.split()[0] != "timestamp":
                continue
            # An explicit NULL exempts the column; NOT NULL does not. Matching a
            # bare "null" substring would treat "NOT NULL" as the exemption and
            # pass exactly the declaration this test exists to reject.
            explicitly_nullable = re.search(r"(?<!not )\bnull\b", tokens) is not None
            assert "default" in tokens or explicitly_nullable, (
                f"{path.name}: `{column}` is a bare TIMESTAMP NOT NULL with no "
                "DEFAULT; the server may attach ON UPDATE CURRENT_TIMESTAMP to it"
            )
