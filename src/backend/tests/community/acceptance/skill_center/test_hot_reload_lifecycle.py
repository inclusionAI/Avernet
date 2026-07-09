"""Route-B acceptance: skill_center full physical hot-reload lifecycle.

Starts a real --local backend, runs HOT_RELOAD_LIFECYCLE end-to-end, and
asserts physical artifacts (dirs + bot-nas symlinks). Off by default; enable
with RUN_ACCEPTANCE=1 (the live_backend fixture also requires 8888 free).
"""
import pytest

from tests.community._flows.skill_center.hot_reload_lifecycle import HOT_RELOAD_LIFECYCLE
from tests.community.framework.flow_runner_live import run_flow_live


@pytest.mark.acceptance
def test_skill_center_hot_reload_lifecycle(live_backend, acceptance_fs_root):
    """create bot → skillset → git skill → add-to-set, with FS artifacts."""
    ctx = run_flow_live(HOT_RELOAD_LIFECYCLE, base_url=live_backend, fs_root=acceptance_fs_root)
    assert "bot_id" in ctx
    assert "skill_set_id" in ctx
    assert "git_skill_id" in ctx
