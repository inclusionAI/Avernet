"""Smoke tests for the authn dependency-port protocols (Rule 25)."""

from __future__ import annotations

from gateway.community.spi.authn import (
    AppTokenRecord,
    AppTokenValidator,
    TenantResolver,
)


def test_records_have_required_fields() -> None:
    rec = AppTokenRecord(
        app_id="cid",
        app_name="Cid App",
        owners="org-1",
        app_type="assistant",
        tenant="t-1",
    )
    assert rec.app_id == "cid"
    assert rec.tenant == "t-1"


def test_protocols_are_importable() -> None:
    for proto in (AppTokenValidator, TenantResolver):
        assert proto is not None
