"""Pytest configuration for all tests.

Integration tests now always use the DI bootstrap container with
SQLite backend (it-sqlite overlay).  The ``bootstrap_init`` session
fixture initialises the full ApplicationContainer with all plugins
in stub mode and an in-memory SQLite engine.

Unit tests are unaffected — they don't depend on bootstrap fixtures.
"""

import pytest

pytest_plugins = [
    "tests.integration.fixtures.bootstrap",
]


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests (deselect with '-m \"not integration\"')",
    )
    config.addinivalue_line(
        "markers",
        "e2e: marks tests as end-to-end API tests (requires running app)",
    )
    config.addinivalue_line(
        "markers",
        "requires_app: marks tests that require a running application instance",
    )
