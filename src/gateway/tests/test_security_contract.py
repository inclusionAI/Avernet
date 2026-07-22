"""Unit test for the per-route security metadata helper."""

from __future__ import annotations

from gateway.community.adapters.web.contracts import user_principal


def test_user_principal_shape() -> None:
    assert user_principal() == {"x-avernet-security": [{"first_party_user": {}}]}


def test_user_principal_returns_fresh_dict() -> None:
    # Mutating one route's metadata must not leak into another's.
    a = user_principal()
    a["x-avernet-security"].append({"tampered": {}})
    assert user_principal() == {"x-avernet-security": [{"first_party_user": {}}]}
