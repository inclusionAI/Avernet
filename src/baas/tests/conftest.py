"""Pytest configuration for all tests.

Integration tests now always use the DI bootstrap container with
SQLite backend (it-sqlite overlay).  The ``bootstrap_init`` session
fixture initialises the full ApplicationContainer with all plugins
in stub mode and an in-memory SQLite engine.

Unit tests are unaffected — they don't depend on bootstrap fixtures.
"""

pytest_plugins = [
    "tests.integration.fixtures.bootstrap",
]
