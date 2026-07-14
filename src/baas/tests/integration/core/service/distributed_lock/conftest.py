"""Shared fixtures for lock integration tests.

Uses bootstrap DI container for database fixtures.
"""

import pytest


@pytest.fixture(scope="session")
def skip_if_zdas_unavailable():
    pass
