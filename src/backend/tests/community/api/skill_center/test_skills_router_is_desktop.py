"""Regression guard: upload_skill path routing by bot_type.

Verifies that the is_desktop flag flows correctly from _get_path_params
all the way to path_factory, producing the right local_dir per bot_type.

Critical cases:
- bot_type=desktop  → engine-view path /home/admin/.openclaw/workspace/skills/skills-local
- bot_type=service  → cloud OSS-view path (NOT engine-view)
  Important: service bots also have device_provider=baas but must NOT get desktop path
- bot_type=personal → cloud OSS-view path (NOT engine-view)
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.adapters.http.dependencies import RequestContext
from agentclaw.community.adapters.http.skill_center.skills import _get_path_params
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory

DESKTOP_SKILLS_LOCAL = "/home/admin/.openclaw/workspace/skills/skills-local"


def _make_ctx(user_id="user_001", bot_id="bot_x"):
    return RequestContext(user_id=user_id, bot_id=bot_id)


def _make_repo(bot_type: str):
    repo = MagicMock()
    repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot_x",
        "owner_id": "user_001",
        "active_engine": "openclaw",
        "bot_type": bot_type,
    }
    return repo


@pytest.mark.parametrize("bot_type,expect_desktop", [
    ("desktop", True),
    ("service", False),   # service bots: device_provider=baas but NOT desktop
    ("personal", False),
])
def test_get_path_params_is_desktop_by_bot_type(bot_type, expect_desktop):
    """_get_path_params returns correct is_desktop for each bot_type."""
    ctx = _make_ctx()
    mock_repo = _make_repo(bot_type)
    result = _get_path_params(ctx, None, None, "bot_x", None, bot_repo=mock_repo)

    assert len(result) == 5, f"expected 5-tuple, got {len(result)}"
    _, _, _, _, is_desktop = result
    assert is_desktop is expect_desktop, (
        f"bot_type={bot_type!r}: expected is_desktop={expect_desktop}, got {is_desktop}"
    )


@pytest.mark.parametrize("bot_type,expect_desktop_path", [
    ("desktop", True),
    ("service", False),
    ("personal", False),
])
def test_path_factory_local_dir_routing(bot_type, expect_desktop_path):
    """path_factory produces engine-view path iff is_desktop=True."""
    from agentclaw.community.plugins.local.skill_repo_sync import LocalSkillRepoSyncPlugin
    factory = WorkspacePathFactory(skill_repo_sync=LocalSkillRepoSyncPlugin())
    is_desktop = (bot_type == "desktop")
    local_dir = factory.get_bot_skills_local_dir(
        "user_001", "bot_x", "openclaw", "staff", is_desktop=is_desktop
    )
    path_str = str(local_dir)

    if expect_desktop_path:
        assert path_str == DESKTOP_SKILLS_LOCAL, (
            f"bot_type=desktop: expected engine-view path, got {path_str!r}"
        )
    else:
        assert path_str != DESKTOP_SKILLS_LOCAL, (
            f"bot_type={bot_type!r}: must NOT produce engine-view path, got {path_str!r}"
        )
        assert "skills-local" in path_str


def test_owner_id_takes_priority_over_user_id():
    """When entity_id (owner) is provided as non-staff type, it is used for bot_type lookup.

    Public bot scenario (proj/team entity): entity_id = bot owner's ID,
    ctx.user_id = current viewer. The bot_type belongs to the owner — lookup
    must use owner ID.

    Note: staff-type entity_id is subject to IDOR prevention (must match ctx.user_id),
    so this test uses entity_type=proj to simulate the public-bot access pattern.
    """
    ctx = _make_ctx(user_id="viewer_999")
    mock_repo = MagicMock()
    mock_repo.get_by_id_and_owner.return_value = {"active_engine": "openclaw", "bot_type": "desktop"}

    _get_path_params(ctx, entity_id="owner_001", entity_type="proj", bot_id="bot_x", engine_type=None, bot_repo=mock_repo)

    # The is_desktop lookup must use owner_001 (the owner), not viewer_999 (the current user).
    # (resolve_engine_for_bot may also call get_by_id_and_owner, hence assert_any_call.)
    mock_repo.get_by_id_and_owner.assert_any_call("bot_x", "owner_001")
