"""Native SQL shape and fail-closed MySQL diagnostics (not live-server tests)."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import mysql, sqlite
from sqlalchemy.exc import DatabaseError, OperationalError

from secbaas.community.core.repository.bot_run_queue_chunk import (
    OrmBotRunQueueChunkRepository,
)


def repository(*, dialect="mysql", count=0, warnings=(), commit_error=None):
    session = MagicMock()
    session.bind.dialect.name = dialect
    count_result = MagicMock()
    count_result.scalar_one.return_value = count
    warning_result = MagicMock()
    warning_result.all.return_value = list(warnings)
    session.execute.side_effect = [MagicMock(), count_result, warning_result]
    session.commit.side_effect = commit_error

    @contextmanager
    def orm_session():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return OrmBotRunQueueChunkRepository(
        SimpleNamespace(orm_session=orm_session)
    ), session


def insert(repo):
    return repo.insert_chunk(
        run_id="r", seq=1, chunk_type="delta", content="payload", metadata='{"a":1}'
    )


@pytest.mark.parametrize("dialect", ["mysql", "mariadb", "sqlite"])
def test_native_insert_shape(dialect):
    repo, session = repository(dialect=dialect)
    assert insert(repo) is None
    statement = session.execute.call_args_list[0].args[0]
    compiler = sqlite.dialect() if dialect == "sqlite" else mysql.dialect()
    compiled = statement.compile(dialect=compiler)
    sql = str(compiled)
    if dialect == "sqlite":
        assert sql.startswith("INSERT INTO baas_bot_run_queue_chunk")
        assert "ON CONFLICT (run_id, seq) DO NOTHING" in sql
        assert session.execute.call_count == 1
    else:
        assert sql.startswith("INSERT IGNORE INTO baas_bot_run_queue_chunk")
        assert "ON DUPLICATE KEY UPDATE" not in sql
        assert session.execute.call_count == 2
        assert (
            str(session.execute.call_args_list[1].args[0]) == "SHOW COUNT(*) WARNINGS"
        )
    assert compiled.params == {
        "run_id": "r",
        "seq": 1,
        "chunk_type": "delta",
        "content": "payload",
        "metadata": '{"a":1}',
    }
    assert "metadata_" not in sql
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
    session.commit.assert_called_once_with()


@pytest.mark.parametrize("dialect", ["mysql", "mariadb"])
def test_duplicate_warnings_are_native_noop_without_rollback(dialect):
    repo, session = repository(
        dialect=dialect, count=1, warnings=[("Warning", 1062, "opaque duplicate")]
    )
    assert insert(repo) is None
    assert [str(call.args[0]) for call in session.execute.call_args_list[1:]] == [
        "SHOW COUNT(*) WARNINGS",
        "SHOW WARNINGS",
    ]
    session.add.assert_not_called()
    session.flush.assert_not_called()
    session.rollback.assert_not_called()
    session.commit.assert_called_once_with()


@pytest.mark.parametrize(
    "warnings",
    [
        [("Warning", 1048, "null")],
        [("Warning", 1264, "range")],
        [("Warning", 1265, "truncated")],
        [("Warning", 1366, "invalid")],
        [("Warning", "1062", "unknown code type")],
        [("Warning", 1062, "duplicate"), ("Warning", 1048, "null")],
        [("Warning", 1048, "null"), ("Warning", 1062, "duplicate")],
    ],
)
def test_nonduplicate_or_mixed_warnings_roll_back_before_commit(warnings):
    repo, session = repository(count=len(warnings), warnings=warnings)
    with pytest.raises(DatabaseError, match="warnings") as raised:
        insert(repo)
    for _, code, message in warnings:
        if code != 1062:
            assert str(code) in str(raised.value.orig)
            assert message in str(raised.value.orig)
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()


@pytest.mark.parametrize(
    "count,warnings",
    [
        (1, []),  # max_error_count=0: warning exists but is not stored
        (2, [("Warning", 1062, "stored")]),  # truncated diagnostics
        (1, [("Warning", 1062, "one"), ("Warning", 1062, "two")]),
        (-1, []),
        (None, []),
        ("1", []),
        (True, []),
    ],
)
def test_incomplete_or_invalid_diagnostics_fail_closed(count, warnings):
    repo, session = repository(count=count, warnings=warnings)
    with pytest.raises(DatabaseError, match="warnings"):
        insert(repo)
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()


@pytest.mark.parametrize("phase", ["insert", "count", "warnings", "commit", "rollback"])
def test_database_failures_propagate(phase):
    failure = OperationalError("operation", {}, RuntimeError("unavailable"))
    repo, session = repository(
        count=1,
        warnings=[("Warning", 1048 if phase == "rollback" else 1062, "opaque")],
        commit_error=failure if phase == "commit" else None,
    )
    if phase in ("insert", "count", "warnings"):
        results = list(session.execute.side_effect)
        results[["insert", "count", "warnings"].index(phase)] = failure
        session.execute.side_effect = results
    elif phase == "rollback":
        session.rollback.side_effect = failure
    with pytest.raises(OperationalError) as raised:
        insert(repo)
    assert raised.value is failure
    session.close.assert_called_once_with()


def test_unknown_dialect_fails_without_insert():
    repo, session = repository(dialect="unsupported")
    with pytest.raises(ValueError, match="Unsupported database dialect"):
        insert(repo)
    session.execute.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_called_once_with()


@pytest.mark.parametrize("code", [1048, 1366, 1265])
def test_rejected_warning_keeps_diagnostic_code_and_message(code):
    message = "invalid chunk field"
    repo, session = repository(count=1, warnings=[("Warning", code, message)])
    with pytest.raises(DatabaseError) as raised:
        insert(repo)
    assert str(code) in str(raised.value.orig)
    assert message in str(raised.value.orig)
    session.rollback.assert_called_once_with()
    session.commit.assert_not_called()


def test_incomplete_diagnostics_report_counts_without_duplicate_payload():
    repo, _ = repository(count=2, warnings=[("Warning", 1062, "opaque duplicate")])
    with pytest.raises(DatabaseError) as raised:
        insert(repo)
    assert "reported=2" in str(raised.value.orig)
    assert "stored=1" in str(raised.value.orig)
    assert "opaque duplicate" not in str(raised.value.orig)
