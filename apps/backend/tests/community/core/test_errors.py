"""Unit tests for ``agentclaw.community.core.errors``.

Domain errors carry no HTTP status — the adapter layer owns that
mapping. These tests pin the (class, detail) contract only.
"""
from __future__ import annotations

import pytest

from agentclaw.community.core.errors import (
    Conflict,
    DomainError,
    Forbidden,
    InternalError,
    LoginRedirectRequired,
    NotFound,
    Unauthorized,
    ValidationError,
)


_SUBCLASSES = [
    ValidationError,
    Unauthorized,
    LoginRedirectRequired,
    Forbidden,
    NotFound,
    Conflict,
    InternalError,
]


@pytest.mark.parametrize("cls", _SUBCLASSES)
def test_subclass_detail_round_trip(cls):
    err = cls("boom")
    assert isinstance(err, DomainError)
    assert err.detail == "boom"
    assert str(err) == "boom"


def test_subclasses_inherit_from_domain_error():
    for cls in _SUBCLASSES:
        assert issubclass(cls, DomainError)


def test_domain_errors_carry_no_status_code():
    """Rule 7: domain errors must not leak HTTP status onto core classes.
    The mapping lives in api/app.py instead.
    """
    for cls in [DomainError, *_SUBCLASSES]:
        assert not hasattr(cls, "status_code"), (
            f"{cls.__name__} carries a status_code attribute — HTTP status "
            "is an adapter-layer concern, not a core/ concern."
        )
