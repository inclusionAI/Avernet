"""Parametrized runner: one pytest instance per registered endpoint case.

Lives in ``tests/endpoints/`` (not ``tests/framework/``) for two reasons:

1. **Pytest only collects from ``test_*.py``.** Putting the test
   function in ``tests/framework/task_runner.py`` was a dead-end — pytest
   never collected it, no matter how many cases were registered.
2. **Parametrize must capture a populated registry.** ``tests/endpoints/
   conftest.py`` glob-imports every case file at conftest-load time,
   which runs **before** this module is imported by pytest's normal
   test collection. So when the ``@pytest.mark.parametrize`` decorator
   below evaluates ``ENDPOINT_CASES``, the list already contains
   every registered case.

The runner body is the generic invocation/expectation drive in
``tests/framework/runner._run_case`` — kept there to keep this file
unchanging as case content grows.
"""
from __future__ import annotations

import pytest
from tests.community.framework.case import EndpointCase
from tests.community.framework.registry import ENDPOINT_CASES
from tests.community.framework.runner import _run_case, _run_case_async

# Partition cases by drive mode: default cases run on the sync TestClient path;
# ``drain_background`` cases run on the async path that awaits fire-and-forget
# work. Both lists are captured after conftest has glob-imported every case file
# (see module docstring), so the registry is fully populated here.
_SYNC_CASES = [c for c in ENDPOINT_CASES if not c.drain_background and "rollback" not in c.id]
_ASYNC_CASES = [c for c in ENDPOINT_CASES if c.drain_background and "rollback" not in c.id]


@pytest.mark.parametrize("case", _SYNC_CASES, ids=lambda c: c.id)
def test_endpoint_injection(
    case: EndpointCase,
    app_with_testing_modules,
    world,
) -> None:
    """One individually-reported pytest instance per registered (sync) case.

    The function body is generic — adding a case never requires editing
    this file.
    """
    _run_case(case, app_with_testing_modules, world)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", _ASYNC_CASES, ids=lambda c: c.id)
async def test_endpoint_injection_async(
    case: EndpointCase,
    app_with_testing_modules,
    world,
) -> None:
    """Sibling runner for ``drain_background`` cases — drives the endpoint async
    and awaits the handler's backgrounded work before asserting."""
    await _run_case_async(case, app_with_testing_modules, world)
