"""Scoped isolation guard for the ``community`` infrastructure column (B1).

The community column (``modules_for(DeployProfile.COMMUNITY)``) is built from
the per-concern ``Community*`` modules under ``di/modules/infrastructure/``.
Those modules import **only** ``plugins.community`` (nothing yet, in B1) — never
``plugins.prod`` — so selecting the ``community`` profile never wires a corp
implementation for a decomposed concern.

This test pins that contract two ways:

1. Building an injector from the community column alone succeeds (the community
   modules have no prod dependencies to resolve at install time).
2. The community *source* (the per-concern DI modules + the community plugin
   impls they bind) imports nothing company-internal — it is the only infra
   shipped in the community distribution.

(Per-concern *positive* bindings — that each Protocol resolves to a real
community impl, not a corp one — are pinned by the ``test_community_*_wiring``
suites. Full "community boots importing zero prod" is not a B1 deliverable — the
business base-list modules still import prod; that is the CI ratchet's job,
``test_business_modules_no_prod_imports``.)
"""
from __future__ import annotations

import ast
import pathlib

from injector import Injector

from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.di.profile_modules import modules_for


# NOTE: there is no longer an "unbound decomposed concern" check here. B3–B7 + B6
# filled every per-concern Protocol with a real community impl (Cache / Secret /
# Database / ObjectStorage / MCP Center / MCP auth / SkillCenter / approval /
# OutboundRuleProvider / DeviceSyncDispatcher / DeviceAdapterTransport /
# HealthProbe); ``ModelAPI`` was removed entirely. Those positive bindings are
# pinned by ``test_community_data_infra_wiring`` / ``test_community_b7_wiring`` /
# ``test_community_b6_wiring``. This guard now pins the two things that remain
# scoped to isolation: the community column builds without prod, and the
# community source imports nothing internal.


def test_community_column_builds_without_prod():
    # The community column installs only Community* stub modules — building an
    # injector from it must not require importing/constructing any prod plugin.
    injector = Injector(modules_for(DeployProfile.COMMUNITY))
    assert injector is not None


# ── Source-level isolation: the community subpackage must name nothing internal ──
# A runtime ``sys.modules`` check can't prove this in B1 — importing ``agentclaw.community.di``
# eagerly loads the base-list business modules, 7 of which still import prod
# (tracked by the CI ratchet). But the community *source* must already be clean:
# the per-concern community modules are the only infra column shipped in the
# community distribution, so they must reference no company-internal package.
_SRC = pathlib.Path(__file__).resolve().parents[3] / "src" / "agentclaw"
# Both community source trees ship in the community distribution: the per-concern
# DI modules and the community plugin impls they bind. Neither may name an
# internal package.
_COMMUNITY_DIRS = (
    _SRC / "di" / "modules" / "infrastructure" / "community",
    _SRC / "plugins" / "community",
)
_FORBIDDEN_IMPORT_PREFIXES = (
    "agentclaw.corp.plugins.prod",
    "agentclaw.community.plugins.local",
    "sofapy_base",
    "arca",
    "layotto",
    "mist",
    "daas",
    "ant_skills_scan_sdk",
    "oss2",
)


def _imported_modules(path: pathlib.Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_community_subpackage_imports_nothing_internal():
    offenders: list[str] = []
    for community_dir in _COMMUNITY_DIRS:
        for py in sorted(community_dir.rglob("*.py")):
            for mod in _imported_modules(py):
                if mod.startswith(_FORBIDDEN_IMPORT_PREFIXES):
                    offenders.append(f"{py.name}: imports {mod}")
    assert not offenders, (
        "Community infrastructure modules and plugin impls must not import any "
        "company-internal package (they are the only code shipped to the "
        "community distribution). Offenders:\n  " + "\n  ".join(offenders)
    )
