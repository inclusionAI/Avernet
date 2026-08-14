"""Smoke test: the PrincipalSigner SPI port is importable."""

from __future__ import annotations

from gateway.community.spi.principal_signer import PrincipalSigner


def test_protocol_is_importable() -> None:
    assert PrincipalSigner is not None
