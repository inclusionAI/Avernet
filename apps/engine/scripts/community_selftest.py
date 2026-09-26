#!/usr/bin/env python3
"""openocb engine self-test gate — prove the community engine distribution boots,
packages, and passes its own suite with corp absent.

openocb's ``src/engine`` IS the extracted community tree: ``engine.corp`` exists
nowhere on disk here, so — unlike ocb's monorepo ``build_community_dist.py`` (which
stages a corp-stripped copy out of the corp-containing monorepo) — there is no
staging step and no need to strip a monorepo ``src`` from ``sys.path``. This script
runs three checks directly against this checkout:

  1. **boot** — assert ``engine.corp`` is unimportable, import ``engine.community``
     + the public FastAPI app, and build the community-profile injector.
  2. **packaging** — build a wheel standalone (``uv build --wheel``) and assert it
     ships ``engine/community`` but not ``engine/corp``.
  3. **pytest** — run the whole community suite (``pytest src``). This is the
     enforcing acceptance gate: a shipped test that imports ``engine.corp`` (or a
     corp-only third-party package) fails here.

Mirrors ``src/backend/scripts/community_selftest.py``. Run from ``src/engine``
(the engine project root). Exit 0 on success.

Usage::  python scripts/community_selftest.py [--skip-tests]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

_ENGINE = Path(__file__).resolve().parents[1]   # .../src/engine
_SRC = _ENGINE / "src"


_BOOT_PROOF = """
import importlib.util
assert importlib.util.find_spec('engine.corp') is None, \\
    'engine.corp is importable — this is not a corp-absent community tree'
import engine                          # PEP 420 namespace (community child only)
import engine.community.api.app        # noqa: F401
from engine.community.di.container import build_injector
from engine.community.di.profile import EngineProfile
from engine.community.di.runtime_mode import RuntimeConfig, RuntimeMode
inj = build_injector(
    config=RuntimeConfig(runtime=RuntimeMode.LOCAL, profile=EngineProfile.COMMUNITY)
)
assert inj is not None
assert engine.community.api.app.app is not None
leaked = [m for m in __import__('sys').modules if m.startswith('engine.corp')]
assert not leaked, f'engine.corp imported into community runtime: {leaked}'
print('COMMUNITY_BOOT_OK')
"""


def _run(argv: list[str], env: dict, timeout: int) -> tuple[int, str]:
    proc = subprocess.run(
        argv, capture_output=True, text=True, env=env, cwd=str(_ENGINE), timeout=timeout,
    )
    return proc.returncode, proc.stdout + "\n--- stderr ---\n" + proc.stderr


def verify_boot() -> tuple[bool, str]:
    env = dict(os.environ)
    env["ENGINE_PROFILE"] = "community"
    env["PYTHONPATH"] = str(_SRC)
    rc, out = _run([sys.executable, "-c", _BOOT_PROOF], env, timeout=180)
    return rc == 0 and "COMMUNITY_BOOT_OK" in out, out


def verify_packaging() -> tuple[bool, str]:
    """Prove the community tree builds a wheel standalone that ships engine/community
    but not engine/corp."""
    with tempfile.TemporaryDirectory(prefix="engine-community-wheel-") as d:
        rc, out = _run(
            ["uv", "build", "--wheel", "--out-dir", d], dict(os.environ), timeout=300
        )
        if rc != 0:
            return False, "uv build --wheel failed:\n" + out
        wheels = list(Path(d).glob("*.whl"))
        if not wheels:
            return False, "no wheel produced\n" + out
        names = zipfile.ZipFile(wheels[0]).namelist()
        has_community = any(n.startswith("engine/community/") for n in names)
        has_corp = any(n.startswith("engine/corp/") for n in names)
        ok = has_community and not has_corp
        return ok, f"wheel={wheels[0].name} community={has_community} corp={has_corp}"


def verify_pytest() -> tuple[bool, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(_SRC)
    rc, out = _run(
        [sys.executable, "-m", "pytest", "src", "-q", "-p", "no:cacheprovider"],
        env, timeout=1800,
    )
    # A full run is large — keep the tail (summary + any failures live at the end).
    return rc == 0, out[-6000:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip the (slow) corp-absent pytest run; boot + packaging only.",
    )
    args = ap.parse_args()

    boot_ok, boot_log = verify_boot()
    pkg_ok, pkg_log = verify_packaging()
    print(f"[boot]\n{boot_log}\n[packaging]\n{pkg_log}")
    ok = boot_ok and pkg_ok

    if not args.skip_tests:
        if ok:
            tests_ok, tests_log = verify_pytest()
            ok = ok and tests_ok
            print(f"[pytest src — corp absent]\n{tests_log}")
        else:
            print("[pytest src] SKIPPED — boot/packaging failed first")

    print("COMMUNITY_SELFTEST_OK" if ok else "COMMUNITY_SELFTEST_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
