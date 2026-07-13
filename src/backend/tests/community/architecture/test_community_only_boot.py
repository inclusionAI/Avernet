"""B11 Group 6 (T6.3) — the community dependency set boots with corp absent.

Proves the OSS-0 #2 goal: an install of only the community dependency set (the
shared ``[project.dependencies]`` + the ``community`` group, WITHOUT the ``corp``
group's company-internal packages) is sufficient to import ``agentclaw.community``
and build the community-profile injector.

The main dev venv has the corp packages installed, so we can't test absence by
uninstalling. Instead we spawn a fresh interpreter with a ``sys.meta_path`` finder
that makes every corp-only third-party package unimportable (``ModuleNotFoundError``,
exactly as a community-only install would see them), then import the community
entrypoint and build ``Injector(modules_for(COMMUNITY))``. If any community code
hard-imported a corp package at import time (rather than via a guarded/optional
path), collection of the injector would raise and this test fails.

The stricter proof — corp source FILES physically absent, not just the packages —
is the Group-7 community-dist builder.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

# Third-party packages that ship ONLY in the corp dependency group. A community
# install never has these; community code must reach them only through guarded
# (try/except ImportError) or stubbed (local/) paths.
_CORP_ONLY_PACKAGES = (
    "sofapy_base",
    "ant_sofapy_base",
    "arca",
    "mist",
    "layotto",
    "oss2",
    "daas",
    "daas_sdk",
    "ant_skills_scan_sdk",
    "sofa_tracer",
)

_PROOF = f"""
import sys
_CORP = set({_CORP_ONLY_PACKAGES!r})
class _Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in _CORP:
            raise ModuleNotFoundError(f"corp pkg {{name!r}} blocked (community-only boot proof)")
        return None
sys.meta_path.insert(0, _Block())

# Corp packages must now be unimportable.
try:
    import sofapy_base  # noqa: F401
    raise SystemExit("corp package was importable — blocker failed")
except ModuleNotFoundError:
    pass

# Community entrypoint + logger + local sofapy-stub patch import cleanly.
import agentclaw.community.log  # noqa: F401
import agentclaw.community.main  # noqa: F401

# The community-profile injector builds without any corp package.
from injector import Injector
from agentclaw.community.di.profile import DeployProfile
from agentclaw.community.di.profile_modules import modules_for

injector = Injector(modules_for(DeployProfile.COMMUNITY))
assert injector is not None
print("COMMUNITY_BOOT_OK")
"""


@pytest.mark.unit
def test_community_profile_boots_without_corp_packages():
    env = dict(os.environ)
    env["DEPLOY_PROFILE"] = "community"
    env.setdefault("SERVER_ENV", "dev")
    proc = subprocess.run(
        [sys.executable, "-c", _PROOF],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0 and "COMMUNITY_BOOT_OK" in proc.stdout, (
        "Community profile failed to import/boot with the corp-only packages "
        "absent — some community code hard-imports a corp package instead of "
        "reaching it through a guarded/optional path.\n"
        f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    )
