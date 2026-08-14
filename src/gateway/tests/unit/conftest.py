"""Shared pytest fixtures."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run tests in bare mode without leaking the host's GW_* env vars."""
    monkeypatch.delenv("GATEWAY_RUN_MODE", raising=False)
    monkeypatch.delenv("GATEWAY_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SOFAPY_CONFIG_PATH", raising=False)
    monkeypatch.delenv("SERVER_ENV", raising=False)
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
