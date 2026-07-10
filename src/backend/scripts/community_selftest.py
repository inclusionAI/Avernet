#!/usr/bin/env python3
"""openocb self-test gate — prove the community distribution boots and passes
its own suite with corp absent.

openocb IS the extracted community tree: ``agentclaw.corp`` exists nowhere on
disk here, so — unlike the ocb-monorepo dist-builder — there is no staging step
and no need to strip a monorepo ``src`` from ``sys.path``. This script runs the
three checks directly against this checkout:

  1. **boot** — import the HTTP composition root (including legacy Env validation,
     config-provider registration, and injector build); assert ``agentclaw.corp``
     is unimportable.
  2. **packaging** — the community ``pyproject`` + ``uv.lock`` resolve
     standalone (``uv lock --locked``).
  3. **pytest** — run the whole community suite
     (``DEPLOY_PROFILE=test pytest tests/community``). This is the enforcing
     acceptance gate: a shipped test that imports ``agentclaw.corp`` (or a
     corp-only third-party package) fails here.

Run from ``src/backend`` (the backend project root). Exit 0 on success.

Usage::  python scripts/community_selftest.py [--skip-tests]
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]   # .../src/backend
_SRC = _BACKEND / "src"


_BOOT_PROOF = """
import importlib.util
assert importlib.util.find_spec('agentclaw.corp') is None, \\
    'agentclaw.corp is importable — this is not a corp-absent community tree'
import agentclaw                       # PEP 420 namespace (community child only)
import agentclaw.community.log         # noqa: F401
from agentclaw.community.adapters.http import app as http_app
assert http_app.injector is not None
print('COMMUNITY_BOOT_OK')
"""


def _run(argv: list[str], env: dict, timeout: int) -> tuple[int, str]:
    proc = subprocess.run(
        argv, capture_output=True, text=True, env=env, cwd=str(_BACKEND), timeout=timeout,
    )
    return proc.returncode, proc.stdout + "\n--- stderr ---\n" + proc.stderr


def verify_boot() -> tuple[bool, str]:
    env = dict(os.environ)
    env["DEPLOY_PROFILE"] = "community"
    env["SERVER_ENV"] = "dev"
    env.pop("REAL_SERVER_ENV", None)
    env.pop("ALIPAY_APP_ENV", None)
    env["PYTHONPATH"] = str(_SRC)
    rc, out = _run([sys.executable, "-c", _BOOT_PROOF], env, timeout=120)
    return rc == 0 and "COMMUNITY_BOOT_OK" in out, out


def verify_packaging() -> tuple[bool, str]:
    rc, out = _run(["uv", "lock", "--locked", "--offline"], dict(os.environ), timeout=120)
    return rc == 0, out


def verify_pytest() -> tuple[bool, str]:
    env = dict(os.environ)
    env["DEPLOY_PROFILE"] = "test"          # neutral application-test.yaml overlay
    env.pop("SERVER_ENV", None)
    rc, out = _run(
        [sys.executable, "-m", "pytest", "tests/community", "-q", "-p", "no:cacheprovider"],
        env, timeout=1800,
    )
    # A full run is large — keep the tail (summary + any failures live at the end).
    return rc == 0, out[-6000:]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skip-tests",
        action="store_true",
        help="Skip the (slow) corp-absent pytest tests/community run; boot + packaging only.",
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
            print(f"[pytest tests/community — corp absent]\n{tests_log}")
        else:
            print("[pytest tests/community] SKIPPED — boot/packaging failed first")

    print("COMMUNITY_SELFTEST_OK" if ok else "COMMUNITY_SELFTEST_FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
