"""Coverage-gate tests.

Two layers:

1. **The gate itself**: always-on. Computes today's gaps, diffs them
   against ``coverage_baseline.txt``, fails on any NEW gap (a
   regression) and on any STALE baseline entry (a route that's been
   covered or removed since the baseline was captured).
2. **Pure-helper self-tests**: exercise ``compute_missing_coverage``,
   ``parse_baseline``, and ``diff_against_baseline`` against stub apps
   + synthetic inputs, so the gate's logic is verified without needing
   the full backend route table.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI

from tests.community.framework.case import (
    CaseInput,
    EndpointCase,
    ExpectError,
    ExpectSuccess,
)
from tests.community.framework.coverage_gate import (
    BaselineDiff,
    CoverageGap,
    compute_missing_coverage,
    diff_against_baseline,
    load_baseline,
    parse_baseline,
)


# ---------------------------------------------------------------------------
# The always-on gate
# ---------------------------------------------------------------------------
def test_endpoint_coverage_against_baseline(app_with_testing_modules) -> None:
    """Every live route must either be fully covered (happy + error) or
    appear on ``coverage_baseline.txt``. New regressions fail; stale
    baseline entries also fail so the list shrinks as backfill lands.
    """
    from tests.community.framework.registry import ENDPOINT_CASES

    current = compute_missing_coverage(app_with_testing_modules, ENDPOINT_CASES)
    baseline = load_baseline()
    diff = diff_against_baseline(current, baseline)

    if diff.ok:
        return

    lines: list[str] = []
    if diff.new:
        lines.append(
            f"{len(diff.new)} NEW uncovered endpoint(s) — add happy + error cases "
            f"(see tests/framework/README.md):"
        )
        lines.extend(f"  + {g.render()}" for g in diff.new)
    if diff.stale:
        lines.append(
            f"{len(diff.stale)} STALE baseline entry/entries — delete these lines "
            f"from tests/framework/coverage_baseline.txt:"
        )
        lines.extend(f"  - {g.render()}" for g in diff.stale)
    pytest.fail("\n".join(lines))


# ---------------------------------------------------------------------------
# Pure-helper tests — stub app, synthetic cases
# ---------------------------------------------------------------------------
@pytest.fixture
def stub_two_endpoints() -> FastAPI:
    """Tiny app with two endpoints — enough to exercise every branch."""
    app = FastAPI()

    @app.get("/a")
    async def a():
        return {}

    @app.post("/b")
    async def b():
        return {}

    return app


def _ok(method: str, path: str, scenario: str = "ok") -> EndpointCase:
    return EndpointCase(
        method=method, path=path, scenario=scenario, expect=ExpectSuccess()
    )


def _err(method: str, path: str, scenario: str = "err") -> EndpointCase:
    return EndpointCase(
        method=method, path=path, scenario=scenario, expect=ExpectError(status=404)
    )


def test_no_cases_reports_every_route_missing_both(stub_two_endpoints) -> None:
    gaps = compute_missing_coverage(stub_two_endpoints, [])
    assert len(gaps) == 2
    assert all(set(g.missing) == {"happy", "error"} for g in gaps)


def test_happy_only_reports_missing_error(stub_two_endpoints) -> None:
    cases = [_ok("GET", "/a"), _ok("POST", "/b")]
    gaps = compute_missing_coverage(stub_two_endpoints, cases)
    assert len(gaps) == 2
    assert all(g.missing == ("error",) for g in gaps)


def test_error_only_reports_missing_happy(stub_two_endpoints) -> None:
    cases = [_err("GET", "/a"), _err("POST", "/b")]
    gaps = compute_missing_coverage(stub_two_endpoints, cases)
    assert len(gaps) == 2
    assert all(g.missing == ("happy",) for g in gaps)


def test_full_coverage_reports_no_gaps(stub_two_endpoints) -> None:
    cases = [
        _ok("GET", "/a"),
        _err("GET", "/a"),
        _ok("POST", "/b"),
        _err("POST", "/b"),
    ]
    assert compute_missing_coverage(stub_two_endpoints, cases) == []


def test_partial_coverage_reports_only_affected_routes(stub_two_endpoints) -> None:
    """``/a`` is fully covered, ``/b`` lacks the error case. Only ``/b``
    appears in the gap list.
    """
    cases = [
        _ok("GET", "/a"),
        _err("GET", "/a"),
        _ok("POST", "/b"),
    ]
    gaps = compute_missing_coverage(stub_two_endpoints, cases)
    assert len(gaps) == 1
    assert gaps[0] == CoverageGap(method="POST", path="/b", missing=("error",))


def test_unrelated_case_does_not_satisfy_real_routes(stub_two_endpoints) -> None:
    """A case targeting a path the app doesn't expose is silently
    ignored by the gate — it doesn't cover anything and it doesn't
    show up as a route with a missing shape (because it isn't a route).
    Pinning this so future refactors don't accidentally treat the
    registry as authoritative over the live route table.
    """
    cases = [_ok("GET", "/ghost"), _err("GET", "/ghost")]
    gaps = compute_missing_coverage(stub_two_endpoints, cases)
    # Both real routes still missing everything; /ghost is not reported.
    paths_in_gaps = {g.path for g in gaps}
    assert "/ghost" not in paths_in_gaps
    assert paths_in_gaps == {"/a", "/b"}


def test_head_method_is_not_separately_required() -> None:
    """FastAPI auto-adds HEAD for every GET. Authors aren't expected
    to write separate HEAD cases, so HEAD must not appear in the gap
    list even when fully uncovered.
    """
    app = FastAPI()

    @app.get("/x")
    async def x():
        return {}

    gaps = compute_missing_coverage(app, [])
    methods = {g.method for g in gaps}
    assert "HEAD" not in methods
    assert methods == {"GET"}


def test_gap_render_format() -> None:
    """Pin the human-readable rendering — the failure message authors
    will read. Also the wire format of the baseline file.
    """
    g = CoverageGap(method="GET", path="/a", missing=("happy", "error"))
    assert g.render() == "GET /a: missing [happy, error]"

    g2 = CoverageGap(method="POST", path="/b", missing=("error",))
    assert g2.render() == "POST /b: missing [error]"


# ---------------------------------------------------------------------------
# Baseline parser + diff
# ---------------------------------------------------------------------------
def test_parse_baseline_round_trip() -> None:
    """A gap rendered and then parsed must equal the original."""
    g = CoverageGap(method="DELETE", path="/api/x/{id}", missing=("happy", "error"))
    parsed = parse_baseline(g.render())
    assert parsed == [g]


def test_parse_baseline_ignores_comments_and_blanks() -> None:
    text = "\n".join([
        "# tracked debt as of 2026-05-20",
        "",
        "GET /a: missing [happy, error]",
        "   # indented comment",
        "POST /b: missing [error]",
        "",
    ])
    parsed = parse_baseline(text)
    assert parsed == [
        CoverageGap(method="GET", path="/a", missing=("happy", "error")),
        CoverageGap(method="POST", path="/b", missing=("error",)),
    ]


def test_parse_baseline_rejects_malformed_lines() -> None:
    with pytest.raises(ValueError, match="Unparseable baseline line"):
        parse_baseline("this is not a gap line")


def test_diff_no_changes_is_ok() -> None:
    g = CoverageGap(method="GET", path="/a", missing=("happy", "error"))
    diff = diff_against_baseline([g], [g])
    assert diff.ok
    assert diff.new == ()
    assert diff.stale == ()


def test_diff_reports_new_gaps() -> None:
    """A gap present today but not on the baseline is a regression."""
    baseline = [CoverageGap("GET", "/a", ("happy", "error"))]
    current = [
        CoverageGap("GET", "/a", ("happy", "error")),
        CoverageGap("POST", "/new", ("happy", "error")),  # newly added route
    ]
    diff = diff_against_baseline(current, baseline)
    assert not diff.ok
    assert diff.new == (CoverageGap("POST", "/new", ("happy", "error")),)
    assert diff.stale == ()


def test_diff_reports_stale_baseline_entries() -> None:
    """A baseline entry that no longer matches must surface so the
    author can delete it — keeps the baseline accurate.
    """
    baseline = [
        CoverageGap("GET", "/a", ("happy", "error")),
        CoverageGap("GET", "/covered_since", ("happy", "error")),  # now covered
    ]
    current = [CoverageGap("GET", "/a", ("happy", "error"))]
    diff = diff_against_baseline(current, baseline)
    assert not diff.ok
    assert diff.new == ()
    assert diff.stale == (CoverageGap("GET", "/covered_since", ("happy", "error")),)


def test_diff_partial_progress_surfaces_as_both_new_and_stale() -> None:
    """A route on the baseline as 'missing [happy, error]' that today
    is only missing happy will appear in BOTH stale (old entry no
    longer matches) AND new (today's shape isn't on the baseline).
    Authors update the line; the gate then goes green.
    """
    baseline = [CoverageGap("GET", "/x", ("happy", "error"))]
    current = [CoverageGap("GET", "/x", ("error",))]  # happy added, error still missing
    diff = diff_against_baseline(current, baseline)
    assert diff.new == (CoverageGap("GET", "/x", ("error",)),)
    assert diff.stale == (CoverageGap("GET", "/x", ("happy", "error")),)


