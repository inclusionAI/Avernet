"""Fixtures for bot_dormant core tests.

Re-exports the framework ``world`` fixture so tests in this package can
use ``world.get(DatabasePlugin)`` to obtain the per-test SQLite plugin
— identical to how endpoint tests seed / inspect via the DI graph.
"""
from tests.community.framework.fixtures import app_with_testing_modules, world  # noqa: F401
