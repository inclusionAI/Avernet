"""Exhaustive eager-check: every binding in the test injector must resolve.

Goes beyond ``eager_check_critical_bindings`` (which only resolves the
explicit list in ``container.py``). This test walks the entire binding
registry exposed by ``injector.binder._bindings`` and calls
``injector.get(key)`` on each. Any provider body crash, ``binder.bind``
type-hint resolution failure, or cycle surfaces here instead of on a
first user request.

Caveats:
- The walk uses ``injector.binder._bindings`` — a private API of
  python-injector, stable across the 0.x releases we use. We guard
  against silent breakage by asserting a non-trivial binding count
  (see ``MIN_EXPECTED_BINDINGS``).
- ``Injector`` and ``Binder`` themselves are bound but are framework
  internals; we skip them.
- Bindings whose provider intentionally raises in the test
  environment can be added to ``ALLOWED_TO_FAIL`` with a one-line
  justification. Keep this list tight — every entry weakens the test.
"""
from __future__ import annotations

import pytest


# Framework-owned bindings the injector creates for itself; resolving
# them is meaningless to a wiring sanity check.
_FRAMEWORK_BINDINGS = {
    "injector.Injector",
    "injector.Binder",
}


# Test-environment-only escape hatch. Add an entry here ONLY if a
# binding is verified to work in prod but cannot resolve in the
# SQLite/local test setup. Format: "<module>.<qualname>": "reason".
ALLOWED_TO_FAIL: dict[str, str] = {
    # GitSyncService resolves the skills-repo URL from the secret store and
    # fails construction when it's absent — which it is in the test profile
    # (LocalSecretResolver returns None). It works in corp where the secret is
    # provisioned; lifecycle discovery tolerates the failure and skips it.
    "agentclaw.community.core.skill_center.services.git_sync.GitSyncService":
        "needs the skills-repo URL secret (absent in test); resolves in corp.",
    "agentclaw.community.api.git_sync_service.GitSyncServiceProtocol":
        "binds to GitSyncService (see above) — same secret requirement.",
    # B11: ArcaSandboxConfig requires an `arca_sandbox` user_config block, which is a
    # corp-only concept. The neutral community test overlay (application-test.yaml)
    # intentionally omits it — community has no ARCA runtime. It resolves under corp
    # env overlays / sofapy, where arca_sandbox is provided.
    "agentclaw.corp.di.config_corp.ArcaSandboxConfig":
        "arca_sandbox is corp-only; the neutral community test overlay omits it.",
}


# Defensive floor: at the time this test was written the test injector
# held ~114 bindings. If a future python-injector upgrade renames
# ``binder._bindings`` (or empties it for some other reason), the loop
# silently runs zero times and the test passes meaninglessly. The floor
# below catches that. Bump it as the graph genuinely grows; never lower
# it without understanding why.
MIN_EXPECTED_BINDINGS = 110


def _qualname(key: type) -> str:
    return f"{key.__module__}.{key.__qualname__}"


def test_every_binding_resolves(test_injector):
    """Resolve every registered binding to surface wiring bugs at test time."""
    bindings = list(test_injector.binder._bindings.keys())

    assert len(bindings) >= MIN_EXPECTED_BINDINGS, (
        f"Found only {len(bindings)} bindings on the test injector — "
        f"expected at least {MIN_EXPECTED_BINDINGS}. Either the test "
        f"setup is broken, or python-injector renamed ``_bindings``; "
        f"if the latter, update this test to use the new API."
    )

    failures: list[str] = []
    for key in bindings:
        name = _qualname(key)
        if name in _FRAMEWORK_BINDINGS:
            continue
        try:
            test_injector.get(key)
        except Exception as exc:  # noqa: BLE001 — aggregate report
            if name in ALLOWED_TO_FAIL:
                continue
            failures.append(f"{name}: {type(exc).__name__}: {exc}")

    if failures:
        joined = "\n  ".join(failures)
        pytest.fail(
            f"{len(failures)} binding(s) failed to resolve in the test "
            f"injector. Either fix the wiring, or add the key to "
            f"ALLOWED_TO_FAIL with a justification:\n  {joined}"
        )
