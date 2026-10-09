"""The admin router's outcome→status table must cover every migration outcome.

``adapters/web/admin.py`` maps :class:`MigrationOutcome` values to HTTP statuses
through a table of string literals rather than by importing the enum — adapters
may not import ``community.core``, and the migration subsystem is meant to be
deleted whole. That indirection is only safe if something keeps the two sides in
step, which is this file: an outcome added upstream fails here instead of
silently falling through to the table's generic 400.
"""

from __future__ import annotations

from gateway.community.adapters.web.admin import _MIGRATION_ERROR_STATUS
from gateway.community.api.baas_migration import MigrationOutcome


def test_every_refusal_outcome_has_a_status() -> None:
    refusals = {
        str(outcome)
        for outcome in MigrationOutcome
        if outcome is not MigrationOutcome.MIGRATED
    }
    assert refusals == set(_MIGRATION_ERROR_STATUS)


def test_success_is_not_in_the_error_table() -> None:
    """A success reaching the error path would be reported as a failure."""
    assert str(MigrationOutcome.MIGRATED) not in _MIGRATION_ERROR_STATUS


def test_statuses_are_client_errors_with_distinct_codes() -> None:
    """Every refusal is the caller's to act on, so none of them is a 5xx.

    Subcodes are distinct *within* a status because the response's ``code`` is
    ``status * 1000 + subcode``: two refusals sharing both would be
    indistinguishable to a client routing on that number, which is exactly what
    ``already_migrated`` (stop) and ``app_name_taken`` (retry differently) must
    never be.
    """
    seen: set[int] = set()
    for outcome, (status, subcode) in _MIGRATION_ERROR_STATUS.items():
        assert 400 <= status < 500, outcome
        code = status * 1000 + subcode
        assert code not in seen, f"duplicate response code {code} at {outcome}"
        seen.add(code)
