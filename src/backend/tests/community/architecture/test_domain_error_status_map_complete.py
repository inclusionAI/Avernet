"""Every concrete DomainError subclass must have a status-code entry.

Since ``agentclaw.community.core.errors`` no longer carries HTTP status codes
(Rule 7 — core/ is transport-free), the mapping lives in
``agentclaw.community.adapters.http.app._DOMAIN_ERROR_STATUS_MAP``. The handler falls
back to 500 for unmapped subclasses, which is forgiving but
silently wrong — adding a new ``NotFound``-style class without a map
entry would emit 500 instead of 404.

This test enumerates every subclass of ``DomainError`` discoverable
under ``agentclaw.community.core.errors`` and asserts the map covers them all.
"""
from __future__ import annotations

import pytest

import agentclaw.community.core.errors as errors_mod
from agentclaw.community.core.errors import DomainError


def _concrete_subclasses(base: type) -> set[type]:
    """Walk every subclass of ``base`` recursively (excluding ``base`` itself)."""
    seen: set[type] = set()
    stack: list[type] = list(base.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls in seen:
            continue
        seen.add(cls)
        stack.extend(cls.__subclasses__())
    return seen


def test_status_map_covers_every_domain_error_subclass():
    # Importing api.app wires up the DI container at module load time;
    # that's expensive and not what we want to verify here. Instead we
    # read the map directly. If the import path changes, update this.
    from agentclaw.community.adapters.http.app import _DOMAIN_ERROR_STATUS_MAP

    # Force-import errors_mod so subclasses are registered on DomainError.
    assert errors_mod  # silence unused import

    subclasses = _concrete_subclasses(DomainError)
    missing = sorted(
        c.__name__ for c in subclasses if c not in _DOMAIN_ERROR_STATUS_MAP
    )
    if missing:
        pytest.fail(
            "DomainError subclasses missing from "
            "agentclaw.community.adapters.http.app._DOMAIN_ERROR_STATUS_MAP "
            "(unmapped subclasses fall back to HTTP 500 — almost always "
            "wrong). Add an entry for: " + ", ".join(missing)
        )
