"""Registry + ``@endpoint_test`` decorator.

The decorator is the Python equivalent of a Java ``@Test`` annotation:
it appends an :class:`EndpointCase` to the module-level
``ENDPOINT_CASES`` list at import time. The reflective runner then
expands that list into individually-reported parametrized test
instances.

The decorated function's body is **not** invoked by the framework —
the framework owns invocation, which is what prevents a case from
falsely claiming coverage of an endpoint it never calls. Authors
should leave the body empty (or use a docstring) and rely on the
decorator kwargs to declare the case.
"""
from __future__ import annotations

import inspect
from typing import Callable, Mapping, Tuple

from tests.community.framework.case import (
    CaseInput,
    EndpointCase,
    Expectation,
    ExtraAssertion,
    HttpMethod,
    SeedCallable,
)


# Module-level registry. Populated at import time by ``@endpoint_test``;
# read by the parametrized runner. Keep this list, not a set, so the
# parametrize order is deterministic across runs.
ENDPOINT_CASES: list[EndpointCase] = []

# Tracks where each ``(method, path, scenario)`` was registered, so the
# duplicate error message can point at both sites.
_REGISTRATION_SITES: dict[Tuple[str, str, str], str] = {}


def _caller_location(depth: int = 2) -> str:
    """Return ``"file.py:lineno"`` for the call ``depth`` frames up.

    Used purely for diagnostics — pinpointing duplicate registrations.
    Best-effort: falls back to ``"<unknown>"`` if introspection fails.
    """
    try:
        frame = inspect.stack()[depth]
        return f"{frame.filename}:{frame.lineno}"
    except Exception:  # pragma: no cover — diagnostic-only
        return "<unknown>"


def endpoint_test(
    *,
    method: HttpMethod,
    path: str,
    scenario: str,
    expect: Expectation,
    input: CaseInput | None = None,
    seed: SeedCallable | None = None,
    extra_assertions: Tuple[ExtraAssertion, ...] = (),
    drain_background: bool = False,
) -> Callable[[Callable[..., None]], Callable[..., None]]:
    """Register an endpoint test case.

    Returns the decorated function unchanged so its name remains usable
    for IDE navigation; the function body itself is never invoked by
    the framework.

    Raises ``ValueError`` if a case with the same
    ``(method, path, scenario)`` is already registered, naming both
    registration sites in the message.
    """

    def decorator(fn: Callable[..., None]) -> Callable[..., None]:
        case = EndpointCase(
            method=method,
            path=path,
            scenario=scenario,
            expect=expect,
            input=input if input is not None else CaseInput(),
            seed=seed,
            extra_assertions=extra_assertions,
            drain_background=drain_background,
        )
        key = (case.method, case.path, case.scenario)
        if key in _REGISTRATION_SITES:
            prior = _REGISTRATION_SITES[key]
            here = _caller_location(depth=2)
            raise ValueError(
                f"Duplicate endpoint_test registration for "
                f"{case.method} {case.path} :: {case.scenario}\n"
                f"  first registered at: {prior}\n"
                f"  also registered at:  {here}"
            )
        _REGISTRATION_SITES[key] = _caller_location(depth=2)
        ENDPOINT_CASES.append(case)
        return fn

    return decorator


def _reset_registry_for_tests() -> None:
    """Test-only helper: empty the registry between tests.

    The framework's own self-tests need to register fixture cases
    without polluting the real registry that the endpoint-runner reads.
    Not part of the public surface.
    """
    ENDPOINT_CASES.clear()
    _REGISTRATION_SITES.clear()
