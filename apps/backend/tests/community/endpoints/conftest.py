"""Auto-discovery conftest for endpoint cases.

Two responsibilities:

1. **Glob-import every ``test_*.py`` under this tree at conftest-load
   time** so each module's ``@endpoint_test`` decorators fire and
   append to ``ENDPOINT_CASES`` **before** pytest evaluates the
   ``@pytest.mark.parametrize`` on the runner. Pytest's own
   test-collection would import these files eventually, but only the
   ones in the current run's node set — running a single node-id
   would skip the rest and leave ``ENDPOINT_CASES`` partially
   populated. The explicit glob makes registration deterministic.

2. **Re-export** the framework fixtures (``app_with_testing_modules``,
   ``world``) and the parametrized runner (``test_endpoint_injection``)
   so case files in this tree can request the fixtures by name and
   pytest discovers the runner in this collection scope.
"""
from __future__ import annotations

# ── Trigger endpoint case-module discovery ─────────────────────────────────
# Shared with ``tests/framework/conftest.py`` so the registry is fully
# populated regardless of which subset of tests pytest is running.
# Idempotent via ``sys.modules`` — safe to call from both conftests.
from tests.community.framework._case_discovery import load_endpoint_case_modules

# ── Re-export framework fixtures into this collection scope ────────────────
# Pytest auto-discovers fixture names imported into a conftest module.
from tests.community.framework.fixtures import (  # noqa: F401
    app_with_testing_modules,
    async_client,
    world,
)


load_endpoint_case_modules()
