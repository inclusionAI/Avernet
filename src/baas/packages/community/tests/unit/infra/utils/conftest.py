"""Shared fixtures for utils unit tests."""

import pytest


@pytest.fixture
def mock_config(monkeypatch):
    """Mock get_config for env_utils tests."""

    class MockConfig:
        user_config = {"app": {"local_debug": False}}

    def _get_config():
        return MockConfig()

    return _get_config
