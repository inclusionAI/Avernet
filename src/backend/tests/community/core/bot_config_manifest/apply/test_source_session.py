"""Tests for the per-apply source session (``apply/source_session.py``, W7).

The session is where one apply's named-source state lives — the checkout
cache, the resolutions the report will carry, and the strict-mode baselines
read back from the last apply's report. It hangs on ``ApplyContext`` the same
way ``budget`` does: mutable by design inside a frozen context, because the
alternatives were per-fetcher state (the fetcher is a DI singleton — state
there leaks across applies) or re-derivation per entry (which would break
"one {git, ref} pulled once per apply").
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agentclaw.community.core.bot_config_manifest.apply.outcomes import (
    SourceResolution,
)
from agentclaw.community.core.bot_config_manifest.apply.source_session import (
    SourceSession,
)
from agentclaw.community.core.bot_config_manifest.fetch.git_source import (
    GitSourceSpec,
)
from agentclaw.community.core.bot_config_manifest.fetch.guarded_fetcher import (
    FetchFailedError,
)


class FakeGitClient:
    """The git seam, scripted per (url, ref); records what it was asked."""

    def __init__(
        self, *, result: object = None, error: Exception | None = None
    ) -> None:
        self.requests: list[GitSourceSpec] = []
        self.headers: list[dict] = []
        self._result = result
        self._error = error

    def fetch(self, spec, *, headers=None):
        self.requests.append(spec)
        self.headers.append(dict(headers or {}))
        if self._error is not None:
            raise self._error
        assert self._result is not None
        return self._result


CHECKOUT = SimpleNamespace(sha="a" * 40, root=Path("/tmp/x"), url="u", ref="main")


def _spec(url: str = "https://git.corp/r.git", ref: str = "main") -> GitSourceSpec:
    return GitSourceSpec(url=url, ref=ref)


def test_one_url_ref_pair_is_fetched_once_and_freshness_is_reported():
    git = FakeGitClient(result=CHECKOUT)
    session = SourceSession(sources={}, baselines={}, git=git)
    first, fresh = session.checkout(_spec(), headers={}, display="src")
    second, again = session.checkout(_spec(), headers={}, display="src")
    # Same checkout object back, one underlying fetch for the pair — and only
    # the first caller is told it moved the bytes, so the ledger charges once.
    assert first is second
    assert fresh is True
    assert again is False
    assert len(git.requests) == 1
    # Nothing recorded yet: a checkout is not a resolution until it is
    # adopted after the strict gate.
    assert session.resolution_records() == ()


def test_adoption_records_the_resolution_once_per_display():
    git = FakeGitClient(result=CHECKOUT)
    session = SourceSession(sources={}, baselines={}, git=git)
    checkout, _ = session.checkout(_spec(), headers={}, display="src")
    session.adopt(display="src", spec=_spec(), checkout=checkout, auth_name="ci")
    for _ in range(2):
        session.adopt(display="src", spec=_spec(), checkout=checkout, auth_name="ci")
    assert session.resolution_records() == (
        SourceResolution(
            name="src", ref="main", resolved_sha="a" * 40, auth="ci"
        ),
    )


def test_distinct_refs_or_urls_fetch_distinctly():
    git = FakeGitClient(result=CHECKOUT)
    session = SourceSession(sources={}, baselines={}, git=git)
    session.checkout(_spec(), headers={}, display="src")
    session.checkout(_spec(ref="dev"), headers={}, display="src")
    session.checkout(
        _spec(url="https://git.corp/other.git"), headers={}, display="src2"
    )
    assert len(git.requests) == 3


def test_a_fetch_failure_is_raised_and_caches_nothing():
    git = FakeGitClient(error=FetchFailedError("git fetch failed"))
    session = SourceSession(sources={}, baselines={}, git=git)
    try:
        session.checkout(_spec(), headers={}, display="src")
        raise AssertionError("expected FetchFailedError")
    except FetchFailedError:
        pass
    # The failure is not cached and nothing is adopted: the next entry's
    # attempt re-asks the client (and, in production, may still fall back
    # per keep_last), and the report of this apply carries no resolution for
    # a source it could not reach.
    session_again = SourceSession(sources={}, baselines={}, git=git)
    assert session_again.resolution_records() == ()


def test_close_is_idempotent_and_deregisters(monkeypatch):
    removed: list[Path] = []
    monkeypatch.setattr(
        "agentclaw.community.core.bot_config_manifest.apply.source_session._rmtree",
        lambda root, **kw: removed.append(Path(root)),
    )
    git = FakeGitClient(result=CHECKOUT)
    session = SourceSession(sources={}, baselines={}, git=git)
    session.checkout(_spec(), headers={}, display="src")
    session.close()
    session.close()
    assert removed == [Path("/tmp/x")]


def test_baseline_reads_the_map_not_a_repository():
    session = SourceSession(
        sources={}, baselines={"src": "b" * 40}, git=FakeGitClient()
    )
    assert session.baseline("src") == "b" * 40
    assert session.baseline("unknown") is None
