"""Shared test fixtures for core service unit tests."""

from unittest.mock import MagicMock, patch

import pytest

from secbaas.community.core.database import db_manager


@pytest.fixture(autouse=True)
def _mock_db_manager():
    """Prevent 'Database not initialized' errors in unit tests."""
    original = db_manager._connection_factory
    db_manager._connection_factory = lambda ds: MagicMock()
    with patch.object(db_manager, "orm_session") as mock_orm:
        mock_orm.return_value.__enter__.return_value = MagicMock()
        yield
    db_manager._connection_factory = original
