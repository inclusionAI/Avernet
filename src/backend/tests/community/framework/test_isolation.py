"""Pin the isolation contract the framework promises authors.

Three guarantees must hold:
1. Two cases run sequentially see *different* in-memory DBs — state
   seeded in case A does not bleed into case B.
2. ``app.state.injector`` is swapped per test and restored after, so
   legacy tests using the session-level injector are unaffected.
3. A row seeded via ``world`` is readable via a second engine session
   checkout in the same case — proves ``StaticPool`` connection sharing
   inside one test.
"""
from __future__ import annotations

from sqlalchemy import text

from agentclaw.community.plugin_api.database import DatabasePlugin


# A trivial table whose presence/absence reveals cross-test bleed.
_DDL = "CREATE TABLE IF NOT EXISTS isolation_probe (id INTEGER PRIMARY KEY, v TEXT)"
_INSERT = "INSERT INTO isolation_probe (id, v) VALUES (1, 'present')"
_SELECT = "SELECT v FROM isolation_probe WHERE id = 1"


def _seed_probe(plugin: DatabasePlugin) -> None:
    with plugin.session() as s:
        s.execute(text(_DDL))
        s.execute(text(_INSERT))
        s.commit()


def _read_probe(plugin: DatabasePlugin) -> str | None:
    with plugin.session() as s:
        # Use IF NOT EXISTS via try/except — the test wants to confirm
        # the table is absent in a fresh fixture, but reading from a
        # non-existent table raises OperationalError. Catch it and
        # report "not present".
        try:
            row = s.execute(text(_SELECT)).first()
        except Exception:
            return None
        return row[0] if row else None


def test_case_A_seeds_a_row(app_with_testing_modules, world) -> None:
    """Case A: seed a row. Case B (next test) must NOT see it."""
    plugin = world.get(DatabasePlugin)
    _seed_probe(plugin)
    assert _read_probe(plugin) == "present"


def test_case_B_sees_fresh_db(app_with_testing_modules, world) -> None:
    """Case B: directly after case A, the DB must be empty.

    With per-test injector + ``reset_for_tests()`` + in-memory SQLite,
    case A's row vanished with its engine. If case B observes
    ``"present"`` here, isolation is broken.
    """
    plugin = world.get(DatabasePlugin)
    assert _read_probe(plugin) is None


def test_app_state_injector_is_swapped(app_with_testing_modules, world) -> None:
    """Inside the test, ``app.state.injector`` must be the per-test
    injector — the same identity ``world`` is built from. Outside the
    fixture, it gets restored.
    """
    assert app_with_testing_modules.state.injector is world.injector


def test_static_pool_shares_connection_within_a_case(world) -> None:
    """A row seeded via one session checkout must be readable via a
    *separate* session checkout in the same case. Pins the ``StaticPool``
    contract from the perspective of code reaching through ``world``.
    """
    plugin = world.get(DatabasePlugin)
    with plugin.session() as s1:
        s1.execute(text("CREATE TABLE share_probe (k TEXT)"))
        s1.execute(text("INSERT INTO share_probe (k) VALUES ('v')"))
        s1.commit()
    with plugin.session() as s2:
        row = s2.execute(text("SELECT k FROM share_probe")).first()
        assert row is not None and row[0] == "v"


def test_engine_identity_differs_between_two_cases(app_with_testing_modules, world) -> None:
    """Record this test's engine in a module-global; the partner test
    asserts the next case has a different one.
    """
    from agentclaw.community.plugins.local import database as db_mod

    global _engine_seen_first
    _engine_seen_first = db_mod._engine
    assert _engine_seen_first is not None


_engine_seen_first = None


def test_next_case_has_different_engine_identity(app_with_testing_modules, world) -> None:
    """Pair to the above: the engine here must be a different object."""
    from agentclaw.community.plugins.local import database as db_mod

    assert db_mod._engine is not None
    if _engine_seen_first is not None:
        assert db_mod._engine is not _engine_seen_first, (
            "Per-test injector must produce a fresh engine; got the same "
            "engine object as the previous test."
        )
