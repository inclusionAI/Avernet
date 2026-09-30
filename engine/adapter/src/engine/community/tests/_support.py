"""Shared test-support helpers for the community suite.

``requires_corp`` guards the handful of DI tests that assert *corp-profile*
wiring. The community (open-source) distribution ships without ``engine.corp``
(see ``tests/architecture/test_community_dist_builds.py``), so those tests can
only be exercised in a build that includes corp; here they self-skip instead of
erroring on a missing ``engine.corp`` import.
"""
from __future__ import annotations

import importlib.util

import pytest

CORP_AVAILABLE = importlib.util.find_spec("engine.corp") is not None

requires_corp = pytest.mark.skipif(
    not CORP_AVAILABLE,
    reason="engine.corp is absent from the community distribution; "
    "corp-profile wiring is only exercisable in a corp-inclusive build",
)
