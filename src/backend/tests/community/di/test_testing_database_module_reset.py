"""Verify TestingDatabaseModule produces a fresh engine per injector build.

Pins the per-injector isolation contract: each call to ``build_injector``
that includes ``TestingDatabaseModule`` must resolve ``DatabasePlugin`` to
a ``SqliteDB`` backed by a brand-new in-memory engine. Without this,
per-test fixtures that rebuild the injector would still share the
process-wide engine singleton from ``plugins/local/database.py``
and tests would bleed state.
"""
from __future__ import annotations

from agentclaw.community.di.container import build_injector
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.plugin_api.database import DatabasePlugin
from agentclaw.community.plugins.local import database as db_mod


def _resolved_engine() -> object:
    """Build a fresh injector and return the engine backing its
    ``DatabasePlugin``. The engine is the module-level singleton in
    ``plugins/local/database.py``, which ``TestingDatabaseModule``
    forces to be re-created via ``reset_for_tests()``.
    """
    injector = build_injector(profile=DeployProfile.TEST)
    plugin = injector.get(DatabasePlugin)
    # Touch the plugin so the lazy engine is created before we inspect it.
    with plugin.session() as _s:
        pass
    return db_mod._engine


def test_two_injector_builds_yield_different_engines() -> None:
    engine_a = _resolved_engine()
    engine_b = _resolved_engine()
    assert engine_a is not None and engine_b is not None
    assert engine_a is not engine_b, (
        "TestingDatabaseModule must call reset_for_tests() so each injector "
        "build gets a fresh in-memory engine; the second build returned the "
        "same engine object as the first."
    )
