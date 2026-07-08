"""Architecture guard: no non-neutralized data-infra references in core (B3).

After B3, the data-plane concerns (relational store / cache / object storage /
secret) are expressed through neutral, capability-named Protocols, and ``core/``
+ ``plugin_api/`` carry no vendor product name for them. This guard locks that
in:

1. The vendor-shaped Protocol type ``OssStoragePlugin`` must not appear anywhere
   under ``src/agentclaw/`` — it was renamed to ``ObjectStoragePlugin`` and must
   stay gone (no back-compat alias).
2. Vendor data-infra *product names* (ZDAS / ZCache / Aliyun / oss2 / Mist —
   matched case-sensitively, so impl-class pointers like
   ``ZdasCollaboratorRepository`` are not chased) must not appear in ``core/``
   or ``plugin_api/`` — except in modules owned by a later SDD (B5 harness-LLM,
   B6 device egress / BaaS, B7 SkillCenter), which are explicitly allow-listed
   and tagged so the ratchet tightens when those land.

``layotto`` / ``MOSN`` are deliberately NOT in the term list: they are the
cross-cutting service-mesh transport (owned by the mesh-bypass work, B6/B7), not
a B3 data-plane concern.

The bar (user directive): the domain model and the Plugin Protocol carry 0
references to a non-neutralized data-infra vendor.
"""
from __future__ import annotations

import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_AGENTCLAW_ROOT = _THIS_FILE.parents[3] / "src" / "agentclaw"
_CORE = _AGENTCLAW_ROOT / "community" / "core"
_PLUGIN_API = _AGENTCLAW_ROOT / "community" / "plugin_api"

# (1) Vendor-shaped Protocol type renamed out of the contract — forbidden
# anywhere in the package.
_FORBIDDEN_TYPES = ("OssStoragePlugin",)

# (2) Vendor data-infra product names — forbidden in core/ + plugin_api/ outside
# the allow-list. Case-sensitive (the all-caps / product-cased forms), so
# impl-class mentions (``ZdasCollaboratorRepository``) and helper names
# (``get_secret_from_mist``) are not flagged — only the bare product name is.
# Matching is plain substring: a ratchet guard errs toward false-positive (a
# stray token like ``boss2`` would trip ``oss2``), which is acceptable — the
# fix is to rename the token, never to weaken the guard.
_VENDOR_TERMS = ("ZDAS", "ZCache", "Aliyun", "oss2", "Mist")

# Modules/files that legitimately still name a data-infra vendor because they
# carry coupling owned by a later SDD. Tagged with the owning SDD so the ratchet
# provably tightens when that SDD lands. Paths are relative to the agentclaw
# root; prefixes match a whole module subtree.
_TERM_ALLOWLIST_PREFIXES: dict[str, str] = {
    # B6 cleared the ``core/devices/`` subtree: device egress / ARCA runtime I/O
    # (Mist-signed tokens, ARCA SDK) moved behind SandboxRuntimeClient +
    # OutboundRuleProvider into plugins/prod, so core/devices/ no longer names a
    # data-infra vendor.
}
_TERM_ALLOWLIST_FILES = {
    "core/service_bot/services/baas_service.py": "B6 — BaaS outbound (Mist/layotto)",
    "core/harness/services/llm.py": "B5 — harness LLM token via the legacy Mist secret_utils path",
}


def _py_files(root: pathlib.Path):
    return root.rglob("*.py")


def _allowlisted(rel: str) -> bool:
    if rel in _TERM_ALLOWLIST_FILES:
        return True
    return any(rel.startswith(prefix) for prefix in _TERM_ALLOWLIST_PREFIXES)


@pytest.mark.unit
def test_renamed_object_storage_type_gone_everywhere() -> None:
    # Scan code AND docs (READMEs / context-boundary docs) so the renamed type
    # cannot creep back via a doc reference — these were neutralized too.
    offenders: list[str] = []
    for file in (*_AGENTCLAW_ROOT.rglob("*.py"), *_AGENTCLAW_ROOT.rglob("*.md")):
        text = file.read_text(encoding="utf-8")
        if any(sym in text for sym in _FORBIDDEN_TYPES):
            offenders.append(file.relative_to(_AGENTCLAW_ROOT).as_posix())
    assert not offenders, (
        "Renamed vendor-shaped Protocol type reintroduced (use "
        "ObjectStoragePlugin):\n  " + "\n  ".join(sorted(offenders))
    )


@pytest.mark.unit
def test_no_data_infra_vendor_terms_in_core_and_plugin_api() -> None:
    offenders: list[str] = []
    for root in (_CORE, _PLUGIN_API):
        for file in _py_files(root):
            rel = file.relative_to(_AGENTCLAW_ROOT).as_posix()
            # B11: layers migrate under ``agentclaw/community/<layer>``; strip the
            # prefix so layer-relative allowlist keys ("core/...") still match.
            if rel.startswith("community/"):
                rel = rel[len("community/"):]
            if _allowlisted(rel):
                continue
            text = file.read_text(encoding="utf-8")
            hit = [term for term in _VENDOR_TERMS if term in text]
            if hit:
                offenders.append(f"{rel}: {', '.join(hit)}")
    assert not offenders, (
        "Vendor data-infra product name found in core/ or plugin_api/ (describe "
        "the capability by deploy profile, not the vendor):\n  "
        + "\n  ".join(sorted(offenders))
    )
