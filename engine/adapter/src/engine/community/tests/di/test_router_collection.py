"""Router composition contract for OSS and internal corp profiles.

Community/test expose the open-source OpenClaw + Claude Code surface only.
Corp additionally preserves existing internal production AICoding routes so this
open-source refactor does not break internal deployments.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from engine.community.tests._support import requires_corp


def _route_paths(profile: str) -> set[str]:
    # FastAPI >=0.139 no longer flattens ``include_router`` routes into
    # ``app.routes``; each included router is wrapped in an ``_IncludedRouter``
    # whose real routes live under ``original_router.routes`` (already carrying
    # their full, prefix-applied path). Older FastAPI keeps them flat. Walk both
    # shapes recursively so this contract is version-agnostic.
    code = (
        "import engine.community.api.app as a\n"
        "def _iter(routes):\n"
        "    for r in routes:\n"
        "        orig = getattr(r, 'original_router', None)\n"
        "        children = getattr(orig, 'routes', None) if orig is not None"
        " else getattr(r, 'routes', None)\n"
        "        if children:\n"
        "            yield from _iter(children)\n"
        "        else:\n"
        "            p = getattr(r, 'path', '') or ''\n"
        "            if p:\n"
        "                yield p\n"
        "print('---ROUTES---')\n"
        "print('\\n'.join(sorted(set(_iter(a.app.routes)))))\n"
    )
    community_src_root = Path(__file__).resolve().parents[4]
    pythonpath_entries = [str(community_src_root)]
    # pytest's pythonpath setting mutates sys.path, not necessarily the
    # PYTHONPATH environment variable inherited by subprocesses. Preserve those
    # active import roots so corp-inclusive runs can still import engine.corp.
    pythonpath_entries.extend(p for p in sys.path if p)

    env = {**os.environ, "ENGINE_PROFILE": profile}
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(pythonpath_entries))
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env
    )
    assert result.returncode == 0, result.stderr
    after_marker = result.stdout.split("---ROUTES---", 1)[-1]
    return {line for line in after_marker.splitlines() if line.startswith("/")}


_OSS_SHARED = [
    "/api/claude_code/ws",
    "/api/openclaw/ws",
    "/api/openclaw/config",
    "/api/openclaw/client",
]

_CORP_INTERNAL_AICODING = [
    "/api/ws",
    "/api/aicoding/skills",
    "/data/{path:path}",
]

_FORBIDDEN_AICODING_PREFIXES = (
    "/api/aicoding",
    "/api/ws",
    "/data/",
    "/data{",
)


def _assert_has_oss_surface(paths: set[str]) -> None:
    for p in _OSS_SHARED:
        assert p in paths, f"missing OSS route {p}"


def _assert_no_aicoding(paths: set[str]) -> None:
    leaked = sorted(
        p for p in paths
        if p.startswith(_FORBIDDEN_AICODING_PREFIXES) or p == "/data/{path:path}"
    )
    assert leaked == []


def test_community_and_test_are_oss_scoped_and_match():
    community = _route_paths("community")
    test = _route_paths("test")
    assert community == test
    _assert_has_oss_surface(community)
    _assert_no_aicoding(community)


@requires_corp
def test_corp_preserves_internal_production_aicoding_routes():
    corp = _route_paths("corp")
    _assert_has_oss_surface(corp)
    for p in _CORP_INTERNAL_AICODING:
        assert p in corp, f"missing corp internal route {p}"
