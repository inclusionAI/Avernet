"""Shared test fixtures for core service unit tests."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.database import db_manager


@pytest.fixture(autouse=True)
def _mock_db_manager():
    """Prevent 'Database not initialized' errors in unit tests."""
    original_plugin = db_manager._plugin
    mock_plugin = MagicMock()

    @contextmanager
    def _orm_session():
        yield MagicMock()

    mock_plugin.orm_session = _orm_session
    db_manager._plugin = mock_plugin
    yield
    db_manager._plugin = original_plugin
