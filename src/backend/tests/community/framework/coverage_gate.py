"""Coverage gate: every live endpoint must have a happy + error case.

**Always on**, using a frozen baseline:

- ``coverage_baseline.txt`` lists the routes that were already
  uncovered when the gate was introduced. Those are tracked debt; the
  gate does not fail on them.
- The gate **does** fail on any **new** gap (a route added or
  uncovered after the baseline was captured) — new endpoints must
  ship with happy + error cases.
- The gate **also** fails on **stale baseline entries** (a route that
  used to be uncovered but no longer is). This forces the allowlist
  to shrink as backfill progresses; you cannot leave dead lines
  behind.

To shrink the baseline: add cases for the route, run the suite, and
delete the line the failure tells you to remove. To regenerate the
baseline from scratch (rare — only when the route table changes
drastically): ``python -m tests.community.framework.coverage_gate --regen``.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from fastapi import FastAPI
from fastapi.routing import APIRoute

from tests.community.framework.case import EndpointCase, ExpectError, ExpectSuccess


BASELINE_PATH = pathlib.Path(__file__).parent / "coverage_baseline.txt"


@dataclass(frozen=True)
class CoverageGap:
    """One ``(method, path)`` that lacks the required case shapes."""

    method: str
    path: str
    missing: tuple[str, ...]  # subset of {"happy", "error"}

    def render(self) -> str:
        return f"{self.method} {self.path}: missing [{', '.join(self.missing)}]"


try:  # fastapi >= 0.138 wraps included routers in a lazy proxy
    from fastapi.routing import _IncludedRouter
except ImportError:  # older fastapi flattens routes onto app.routes directly
    _IncludedRouter = ()


def _walk_apiroutes(routes, prefix: str = ""):
    """Yield ``(full_path, APIRoute)`` for every mounted route.

    fastapi >= 0.138 no longer eagerly flattens ``include_router`` results onto
    ``app.routes``; instead each include becomes a lazy ``_IncludedRouter`` whose
    real sub-routes live on ``original_router.routes`` with the include ``prefix``
    applied only at request time. We recompose the prefix ourselves so nested
    includes (e.g. files → resources → app) yield their true live path. On older
    fastapi there are no wrappers, so this degrades to a flat walk with ``prefix=''``.
    """
    for route in routes:
        if _IncludedRouter and isinstance(route, _IncludedRouter):
            inner = prefix + getattr(route.include_context, "prefix", "")
            original = route.original_router
            yield from _walk_apiroutes(getattr(original, "routes", []), inner)
            yield from _walk_apiroutes(getattr(original, "_low_priority_routes", []), inner)
        elif isinstance(route, APIRoute):
            yield prefix + route.path, route


def _live_routes(app: FastAPI) -> set[tuple[str, str]]:
    """Return the live ``(method, path)`` set from an app's APIRoute table.

    Non-``APIRoute`` mounts (websockets, static mounts, etc.) are not
    relevant to the gate and are filtered out.
    """
    routes: set[tuple[str, str]] = set()
    for path, route in _walk_apiroutes(app.routes):
        for method in route.methods:
            # FastAPI auto-adds HEAD for GET; that's not a separately
            # authored endpoint, so don't demand a case for it.
            if method == "HEAD":
                continue
            routes.add((method, path))
    return routes


def _classify(cases: Iterable[EndpointCase]) -> Mapping[tuple[str, str], set[str]]:
    """Group registered cases by route, returning which expectation
    shapes (``"happy"`` / ``"error"``) each route has.
    """
    shapes: dict[tuple[str, str], set[str]] = {}
    for case in cases:
        key = (case.method, case.path)
        kinds = shapes.setdefault(key, set())
        if isinstance(case.expect, ExpectSuccess):
            kinds.add("happy")
        elif isinstance(case.expect, ExpectError):
            kinds.add("error")
    return shapes


def compute_missing_coverage(
    app: FastAPI,
    cases: Iterable[EndpointCase],
) -> list[CoverageGap]:
    """Return the list of ``(method, path)`` routes whose registered
    cases don't include both a happy-path and an error-path scenario.

    Routes with **zero** cases are reported as missing **both**. Routes
    with one shape but not the other are reported as missing only the
    absent shape. Routes with both are not reported.
    """
    shapes = _classify(cases)
    gaps: list[CoverageGap] = []
    for method, path in sorted(_live_routes(app)):
        present = shapes.get((method, path), set())
        missing = tuple(s for s in ("happy", "error") if s not in present)
        if missing:
            gaps.append(CoverageGap(method=method, path=path, missing=missing))
    return gaps


# ---------------------------------------------------------------------------
# Baseline parsing + diff
# ---------------------------------------------------------------------------
# Match the canonical render: ``METHOD /path: missing [happy, error]``.
_BASELINE_LINE = re.compile(
    r"^(?P<method>[A-Z]+)\s+(?P<path>\S+):\s+missing\s+\[(?P<missing>[^\]]+)\]\s*$"
)


def parse_baseline(text: str) -> list[CoverageGap]:
    """Parse the baseline file's text into ``CoverageGap`` entries.

    Comments (``#``-prefixed) and blank lines are ignored so authors can
    annotate context next to specific entries. Order is preserved so
    test failures can refer to line numbers if helpful.
    """
    out: list[CoverageGap] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _BASELINE_LINE.match(line)
        if not m:
            raise ValueError(
                f"Unparseable baseline line: {raw!r}. "
                "Expected: 'METHOD /path: missing [happy, error]'"
            )
        missing = tuple(
            kind.strip() for kind in m.group("missing").split(",") if kind.strip()
        )
        out.append(
            CoverageGap(
                method=m.group("method"),
                path=m.group("path"),
                missing=missing,
            )
        )
    return out


def load_baseline(path: pathlib.Path = BASELINE_PATH) -> list[CoverageGap]:
    """Read and parse the baseline file. Empty if the file is absent."""
    if not path.exists():
        return []
    return parse_baseline(path.read_text())


@dataclass(frozen=True)
class BaselineDiff:
    """Result of comparing current gaps against the baseline.

    - ``new``: gaps present today that are NOT on the baseline. These
      are coverage regressions — the gate fails on these.
    - ``stale``: baseline entries that no longer match today's gaps,
      because the route was either covered or removed. The gate fails
      on these too, with instructions to delete the lines — keeps the
      baseline accurate over time.
    """

    new: tuple[CoverageGap, ...]
    stale: tuple[CoverageGap, ...]

    @property
    def ok(self) -> bool:
        return not self.new and not self.stale


def diff_against_baseline(
    current: Iterable[CoverageGap],
    baseline: Iterable[CoverageGap],
) -> BaselineDiff:
    """Compute new + stale gaps.

    Equality is structural: ``(method, path, missing)`` must match
    exactly. So a route on the baseline as "missing [happy, error]"
    that today is only missing happy will appear in **both** ``stale``
    (the old "missing [happy, error]" entry no longer matches) and
    ``new`` (today's "missing [happy]" isn't on the baseline). This is
    intentional — progress on the route should surface as an
    actionable diff, not be silently absorbed.
    """
    current_set = set(current)
    baseline_set = set(baseline)
    return BaselineDiff(
        new=tuple(sorted(current_set - baseline_set, key=lambda g: (g.method, g.path))),
        stale=tuple(sorted(baseline_set - current_set, key=lambda g: (g.method, g.path))),
    )


def _regen_baseline_cli() -> int:  # pragma: no cover — CLI tooling
    """Rewrite ``coverage_baseline.txt`` with today's gaps.

    For use only when the route table changes drastically and rewriting
    the baseline by hand isn't practical. Run with:

        python -m tests.community.framework.coverage_gate --regen
    """
    # Importing app/registry inside the CLI to keep the module
    # import-light for normal test runs.
    from agentclaw.community.adapters.http.app import app
    from tests.community.framework.registry import ENDPOINT_CASES

    gaps = compute_missing_coverage(app, ENDPOINT_CASES)
    BASELINE_PATH.write_text("\n".join(g.render() for g in gaps) + "\n")
    print(f"Wrote {len(gaps)} baseline entries to {BASELINE_PATH}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys

    if "--regen" in sys.argv:
        sys.exit(_regen_baseline_cli())
    print(__doc__)
    sys.exit(0)
