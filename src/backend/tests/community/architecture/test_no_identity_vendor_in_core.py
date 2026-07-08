"""Architecture guard: no non-neutralized identity references in core (B4).

After B4, the identity concern (Auth / Passport / TokenExchange / AuthRelationship)
is expressed through neutral, capability-named types, and ``core/`` carries no
vendor identity domain model, service name, or field. This guard locks that in:

1. The vendor identity *types* ``BuserviceUser`` / ``TCAuthError`` must not appear
   anywhere under ``src/agentclaw/`` — they were renamed to ``AuthenticatedIdentity``
   / ``PassportError`` and must stay gone (no back-compat alias).
2. Vendor identity *product names* (Buservice / tcauthmng / AgentPass / AceAgent /
   antbuservice) must not appear in ``core/`` — except in files owned by a later
   SDD (B6 device egress, B7 AntProcess / SkillCenter), which are explicitly
   allow-listed and tagged so the ratchet tightens when those land.
3. Vendor identity *field names* (``.tntInstId`` / ``.outUserNo``) must not be read
   in ``core/`` — except the B7 AntProcess facades, which carry them as the corp
   serialization contract.

The bar (user directive): when B4 is done, ``core/`` has 0 references to a
non-neutralized identity domain model / service.
"""
from __future__ import annotations

import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_AGENTCLAW_ROOT = _THIS_FILE.parents[3] / "src" / "agentclaw"
_CORE = _AGENTCLAW_ROOT / "community" / "core"

# (1) Vendor identity types that were renamed out of the contract — forbidden
# anywhere in the package.
_FORBIDDEN_TYPES = ("BuserviceUser", "TCAuthError")

# (2) Vendor identity product names — forbidden in core/ outside the allow-list.
_VENDOR_TERMS = ("Buservice", "tcauthmng", "AgentPass", "AceAgent", "antbuservice")

# core/ files that legitimately still name an identity vendor because they are
# owned by a later SDD. Tagged with the owning SDD so the ratchet provably
# tightens when that SDD lands and removes the file's coupling.
#
# Empty as of B6 Group D: the device egress rules moved to
# ``plugins/prod/outbound_rules.py`` (corp ARCA provider) and the BaaS device /
# outbound surfaces are neutral — core/ now names no identity vendor.
_TERM_ALLOWLIST: dict[str, str] = {}

# (3) Vendor identity field names — forbidden as attribute reads in core/. The
# AntProcess facades that carried these moved to plugins/prod (B7), so core is
# fully clean and there is no allowlist.
_VENDOR_FIELDS = (".tntInstId", ".outUserNo")


def _py_files(root: pathlib.Path):
    return root.rglob("*.py")


@pytest.mark.unit
def test_no_renamed_vendor_identity_types_anywhere() -> None:
    offenders: list[str] = []
    for file in _py_files(_AGENTCLAW_ROOT):
        text = file.read_text(encoding="utf-8")
        if any(sym in text for sym in _FORBIDDEN_TYPES):
            offenders.append(file.relative_to(_AGENTCLAW_ROOT).as_posix())
    assert not offenders, (
        "Renamed vendor identity type reintroduced (use AuthenticatedIdentity / "
        "PassportError):\n  " + "\n  ".join(sorted(offenders))
    )


@pytest.mark.unit
def test_no_vendor_identity_terms_in_core() -> None:
    offenders: list[str] = []
    for file in _py_files(_CORE):
        rel = file.relative_to(_CORE).as_posix()
        if rel in _TERM_ALLOWLIST:
            continue
        text = file.read_text(encoding="utf-8")
        hit = [term for term in _VENDOR_TERMS if term in text]
        if hit:
            offenders.append(f"{rel}: {', '.join(hit)}")
    assert not offenders, (
        "Vendor identity product name found in core/ (use neutral capability "
        "terms — passport service / authorization-relationship service):\n  "
        + "\n  ".join(sorted(offenders))
    )


@pytest.mark.unit
def test_no_vendor_identity_field_reads_in_core() -> None:
    offenders: list[str] = []
    for file in _py_files(_CORE):
        rel = file.relative_to(_CORE).as_posix()
        text = file.read_text(encoding="utf-8")
        hit = [field for field in _VENDOR_FIELDS if field in text]
        if hit:
            offenders.append(f"{rel}: {', '.join(hit)}")
    assert not offenders, (
        "Vendor identity field read in core/ (read the neutral alias instead — "
        ".tenantId / .staffId):\n  " + "\n  ".join(sorted(offenders))
    )
