"""The git baselines a compile starts from: read back out of report history (W7)."""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from agentclaw.community.core.bot_config_manifest.apply.outcomes import ApplyReport, ApplyStatus, SourceResolution
from agentclaw.community.core.bot_config_manifest.compiler.baselines import BASELINE_HISTORY_APPLIES, last_resolutions
from tests.community.core.bot_config_manifest.apply._fakes import FakeManifestContent, make_context, manifest_compiler

URL = "https://git.corp/charts.git"


def _report(resolutions, *, status=ApplyStatus.SUCCEEDED, apply_id="prior") -> ApplyReport:
    return ApplyReport(
        apply_id=apply_id,
        bot_id="b_1",
        trigger="explicit",
        status=status,
        started_at=datetime.now(),
        sources=tuple(resolutions),
    )


def _row(report: ApplyReport) -> object:
    """A duck-typed apply row: every field the codec re-reads off the record."""
    return SimpleNamespace(
        apply_id=report.apply_id,
        bot_id=report.bot_id,
        trigger=report.trigger,
        started_at=report.started_at,
        finished_at=None,
        report=json.dumps(report.as_payload()),
        status=report.status.value,
    )


class _Applies:
    def __init__(self, reports) -> None:
        self.rows = [_row(r) for r in reports]
        self.calls = []

    def recent(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


def test_no_reports_and_reports_without_resolutions_yield_no_baseline() -> None:
    """The reports ARE the baseline table; nothing recorded means no opinion."""
    assert last_resolutions([]) == {}
    assert last_resolutions([_report([])]) == {}


def test_a_recorded_resolution_is_keyed_by_url_ref_and_mode() -> None:
    charts = SourceResolution(name="charts", url=URL, ref="main", mode="strict", resolved_sha="f" * 40, auth="t")
    assert last_resolutions([_report([charts])]) == {(URL, "main", "strict"): "f" * 40}


def test_baselines_are_read_by_url_ref_and_mode_and_skip_incomplete_rows() -> None:
    """The key is the repository, the ref and the mode — never the row's name.

    Two ``from`` names over one repository at one mode are two rows with one
    sha; another ref, or the other mode, is its own key. A row missing
    ``url`` or ``mode`` predates those being recorded and is skipped; one
    with no ``ref`` reads ``HEAD``.
    """
    rows = [
        SourceResolution(name="charts", url=URL, ref="main", mode="non_strict", resolved_sha="f" * 40),
        SourceResolution(name="dashboards", url=URL, ref="main", mode="non_strict", resolved_sha="f" * 40),
        SourceResolution(name="pinned", url=URL, ref="v1", mode="non_strict", resolved_sha="a" * 40),
        SourceResolution(name="strict", url=URL, ref="main", mode="strict", resolved_sha="b" * 40),
        SourceResolution(name="headless", url=URL, ref=None, mode="strict", resolved_sha="c" * 40),
        SourceResolution(name="legacy", url=None, ref="main", mode=None, resolved_sha="d" * 40),
    ]
    assert last_resolutions([_report(rows)]) == {
        (URL, "main", "non_strict"): "f" * 40,
        (URL, "v1", "non_strict"): "a" * 40,
        (URL, "main", "strict"): "b" * 40,
        (URL, "HEAD", "strict"): "c" * 40,
    }


def test_a_failed_apply_does_not_wipe_a_strict_baseline() -> None:
    """The walk is newest-first across the history window, so a failed fetch
    (which adopts nothing) leaves the previous resolution standing."""
    charts = SourceResolution(name="charts", url=URL, ref="main", mode="strict", resolved_sha="f" * 40)
    failed = _report([], status=ApplyStatus.FAILED, apply_id="newest")
    assert last_resolutions([failed, _report([charts])]) == {(URL, "main", "strict"): "f" * 40}
    newer = SourceResolution(name="charts", url=URL, ref="main", mode="strict", resolved_sha="e" * 40)
    assert last_resolutions([_report([newer], apply_id="newest"), _report([charts])]) == {
        (URL, "main", "strict"): "e" * 40
    }


def test_within_one_report_the_later_resolution_is_the_baseline() -> None:
    """A creation folds two phases into one report, carried phase first; the
    later row is the one its delivery stood behind."""
    first = SourceResolution(name="charts", url=URL, ref="main", mode="strict", resolved_sha="1" * 40)
    second = SourceResolution(name="charts", url=URL, ref="main", mode="strict", resolved_sha="2" * 40)
    assert last_resolutions([_report([first, second])]) == {(URL, "main", "strict"): "2" * 40}


def test_the_compiler_reads_the_bots_recent_reports_through_the_codec() -> None:
    """``ManifestCompiler._baselines`` walks the bounded history for this bot."""
    charts = SourceResolution(name="charts", url=URL, ref="main", mode="strict", resolved_sha="f" * 40)
    applies = _Applies([_report([charts])])
    compiler = manifest_compiler(content=FakeManifestContent(), resolver=object(), applies=applies)
    ctx = make_context(bot_id="b_1", entity_id="u_owner")
    assert compiler._baselines(ctx) == {(URL, "main", "strict"): "f" * 40}
    assert applies.calls == [{"env": "dev", "entity_id": "u_owner", "bot_id": "b_1", "limit": BASELINE_HISTORY_APPLIES}]
