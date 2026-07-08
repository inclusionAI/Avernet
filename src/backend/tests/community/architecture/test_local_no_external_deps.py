"""S3 — dependency boundary: plugins/local must not import external /
company-internal service deps. local claims offline-runnable; importing
layotto/arca/sofapy_base/MOSN clients is a corruption source (and a
top-level import can break collection in a no-network env).

AST scans ALL Import/ImportFrom nodes (module-level AND function-local).

Scope: this guard checks only DIRECT imports in ``plugins/local/*.py``;
transitive deps reachable through ``core/`` are out of scope for this
static guard.

The forbidden roots below were derived from what PROD plugins actually
import (these are the external service SDKs that local must avoid):
``arca`` (arca_factory.py, device.py), ``layotto`` (layotto.py),
``sofapy_base`` (database.py / layotto.py / oss_storage.py via
``sofapy_base.app.layotto_manager``), and ``oss2`` (oss_storage.py).
Generic HTTP libs (``requests``, ``httpx``, ``websockets``) are NOT
forbidden — local legitimately uses ``requests`` to probe a local engine.
"""
from __future__ import annotations

import ast
import pkgutil
from pathlib import Path

import pytest

import agentclaw.community.plugins.local as local_pkg

# Forbidden top-level package roots (prefix match on dotted path).
# Each entry = a high-confidence external/company-internal SERVICE dep
# that a local (offline) plugin must never import. Derived from the deps
# PROD plugins import to talk to remote services.
_FORBIDDEN_ROOTS: dict[str, str] = {
    "layotto": "MOSN/Layotto sidecar client — remote service mesh (prod layotto.py)",
    "arca": "Arca sandbox/device service SDK — remote (prod arca_factory.py, device.py)",
    "sofapy_base": "SOFA Python base — wraps Layotto manager -> ZDAS/ZCache (prod database.py)",
    "oss2": "Alibaba OSS object-storage client — remote service (prod oss_storage.py)",
}

# Each entry MUST have a one-line justification (review-level decision).
_ALLOWLIST: dict[str, str] = {
    # "agentclaw.community.plugins.local.some_module:somedep": "reason ...",
}


def _iter_local_modules():
    for mi in pkgutil.iter_modules(local_pkg.__path__):
        if mi.name.startswith("_"):
            continue
        yield mi.name


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                roots.add(a.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # level==0 → absolute import; relative imports (level>0) are
            # intra-package, safe.
            if node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
    return roots


def test_local_plugins_have_no_external_deps():
    failures: list[str] = []

    for mod_name in _iter_local_modules():
        src_path = Path(local_pkg.__path__[0]) / f"{mod_name}.py"
        if not src_path.exists():
            continue
        src = src_path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for root in _imported_roots(tree):
            if root in _FORBIDDEN_ROOTS:
                key = f"agentclaw.community.plugins.local.{mod_name}:{root}"
                if key in _ALLOWLIST:
                    continue
                failures.append(
                    f"{mod_name}.py imports forbidden external dep '{root}' "
                    f"({_FORBIDDEN_ROOTS[root]}); local must stay offline. "
                    f"Add to _ALLOWLIST with a reason only after review."
                )

    if failures:
        pytest.fail("S3 dependency-boundary violations:\n" + "\n".join(failures))
