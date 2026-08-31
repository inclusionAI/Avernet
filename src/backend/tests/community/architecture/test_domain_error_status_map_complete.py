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

import pathlib
import re

import pytest

import agentclaw.community.core.errors as errors_mod
from agentclaw.community.core.errors import DomainError

_THIS_FILE = pathlib.Path(__file__).resolve()
_AGENTCLAW_ROOT = _THIS_FILE.parents[3] / "src" / "agentclaw"

#: Subclasses deliberately absent from the map, because nothing raises them.
#:
#: The rule this file enforces exists so a *reachable* error does not fall
#: back to 500. A class no production path ever raises cannot reach the
#: handler at all, so a map entry for it would be a guess at a status no
#: response will ever carry — and picking one here would be inventing wire
#: behaviour from a test.
#:
#: This is not an escape hatch: ``test_the_exempt_subclasses_are_never_raised``
#: below re-derives the claim from the source tree, so the first ``raise`` of
#: one of these names fails this file until the map names its status.
_NEVER_RAISED: frozenset[str] = frozenset({
    # Declared for the Direct-command-versus-SkillSet ownership contract, but
    # that conflict is still reported as
    # ``SkillSetControlPlaneConflictError("RESOURCE_MANAGED_BY_SKILL_SET")``
    # (see core/repository/implementations/skill_center/direct_installation_commands.py),
    # which is mapped. Nothing raises this class.
    "SkillSetManagedResourceError",
})


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
        c.__name__
        for c in subclasses
        if c not in _DOMAIN_ERROR_STATUS_MAP and c.__name__ not in _NEVER_RAISED
    )
    if missing:
        pytest.fail(
            "DomainError subclasses missing from "
            "agentclaw.community.adapters.http.app._DOMAIN_ERROR_STATUS_MAP "
            "(unmapped subclasses fall back to HTTP 500 — almost always "
            "wrong). Add an entry for: " + ", ".join(missing)
        )


def test_the_exempt_subclasses_are_never_raised():
    """The exemption above must keep earning itself.

    ``_NEVER_RAISED`` is only defensible while it is true. Scanning the backend
    source for a ``raise`` of each exempt name turns the claim into something
    the suite checks rather than something a comment asserts: adding the first
    raise site makes this fail, and the fix is a status entry in the map, not
    another line in the exemption.
    """
    raised: list[str] = []
    for name in sorted(_NEVER_RAISED):
        pattern = re.compile(rf"\braise\s+{re.escape(name)}\b")
        for path in sorted(_AGENTCLAW_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            if pattern.search(text):
                raised.append(f"{name} raised in {path.relative_to(_AGENTCLAW_ROOT)}")
    assert not raised, (
        "these subclasses are exempt from the status map because nothing "
        "raises them, but something now does — give each one an entry in "
        "agentclaw.community.adapters.http.app._DOMAIN_ERROR_STATUS_MAP and "
        "drop it from _NEVER_RAISED:\n" + "\n".join(raised)
    )
