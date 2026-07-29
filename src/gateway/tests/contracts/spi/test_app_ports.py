"""Smoke tests for the app-domain SPI port (``AppRegistry`` contract)."""

from __future__ import annotations

from gateway.community.spi.app import AppRegistry, RegisteredApp


def test_record_has_required_fields() -> None:
    rec = RegisteredApp(
        app_id="app-1",
        app_name="Demo App",
        owners="org-1",
        app_type="assistant",
        tenant="t-1",
    )
    assert rec.app_id == "app-1"
    assert rec.tenant == "t-1"


def test_protocols_are_importable() -> None:
    assert AppRegistry is not None
