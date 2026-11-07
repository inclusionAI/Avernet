"""Git entry selection remains isolated through compilation and Apply."""
from __future__ import annotations

import pytest

from agentclaw.community.core.apply_engine.capabilities import Category
from agentclaw.community.core.apply_engine.outcomes import ApplyStatus, EntryOutcome
from agentclaw.community.core.bot_config_manifest.fetch.git_source import GitCheckout
from tests.community.core.apply_engine._fakes import FakeIdentityService, FakeStartupScriptService, make_context
from tests.community.core.apply_engine.test_apply_engine import _apply, _engine, _outcomes


@pytest.mark.asyncio
async def test_shared_git_selection_refuses_only_selected_link_and_aborts_identity(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "files").mkdir()
    (root / "files" / "soul.md").write_text("safe identity\n")
    (root / "files" / "rules.md").symlink_to("/nonexistent/host/path")
    checkout = GitCheckout(
        root=root,
        sha="a" * 40,
        url="https://example.com/content.git",
        ref="main",
        members=(("100644", "files/soul.md", 14), ("120000", "files/rules.md", 22)),
    )

    class Git:
        def __init__(self):
            self.calls = 0

        def fetch(self, spec, *, headers=None):
            self.calls += 1
            return checkout

    git = Git()
    identity = FakeIdentityService()
    scripts = FakeStartupScriptService()
    engine = _engine(identity=identity, scripts=scripts)
    engine.compiler._git = lambda: git
    report = await _apply(
        engine,
        """schema_version: 1
sources:
  repo:
    protocol: git
    url: https://example.com/content.git
    ref: main
    subpath: files
manifest:
  identity:
    - type: SOUL.md
      from: repo
      subpath: soul.md
    - type: RULES.md
      from: repo
      subpath: rules.md
      on_fetch_failure: keep_last
script:
  body: echo ok
""",
        ctx=make_context(engine_type="openclaw"),
    )

    outcomes = _outcomes(report)
    assert git.calls == 1, report.entries
    assert outcomes["RULES.md"] is EntryOutcome.FAILED
    assert outcomes["SOUL.md"] is EntryOutcome.SKIPPED
    assert identity.writes == []
    assert next(c for c in report.categories if c.construct is Category.IDENTITY).aborted
    assert outcomes["script"] is EntryOutcome.CREATED
    assert report.status is ApplyStatus.PARTIAL
