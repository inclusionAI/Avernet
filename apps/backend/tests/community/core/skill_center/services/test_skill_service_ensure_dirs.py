"""Focused tests for SkillService workspace-directory bootstrap behavior."""

from pathlib import Path
from unittest.mock import MagicMock, patch


from agentclaw.community.core.skill_center.services.skill_service import (
    SkillService,
)


def _build_service(
    *,
    active_dir: Path | MagicMock,
    skill_dirs: dict[str, Path],
    mock_skill_repo: MagicMock,
) -> SkillService:
    return SkillService(
        skill_repo=mock_skill_repo,
        skill_repo_sync=MagicMock(),
        category_repo=MagicMock(),
        market_cache=MagicMock(),
        device_fs_factory=MagicMock(),
        git_sync_service_factory=MagicMock(),
        active_dir=active_dir,
        repo_dir=skill_dirs["repo_dir"],
        local_dir=skill_dirs["local_dir"],
    )


def test_ensure_directories_creates_missing_active_dir(
    skill_dirs, mock_skill_repo
):
    active_dir = skill_dirs["active_dir"] / "workspace-skills"
    assert not active_dir.exists()

    svc = _build_service(
        active_dir=active_dir,
        skill_dirs=skill_dirs,
        mock_skill_repo=mock_skill_repo,
    )

    assert active_dir.is_dir()
    assert svc.active_dir == active_dir


def test_ensure_directories_keeps_existing_active_dir(
    skill_dirs, mock_skill_repo
):
    active_dir = skill_dirs["active_dir"]
    sentinel = active_dir / "partial-state"
    sentinel.write_text("keep", encoding="utf-8")

    _build_service(
        active_dir=active_dir,
        skill_dirs=skill_dirs,
        mock_skill_repo=mock_skill_repo,
    )

    assert active_dir.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_ensure_directories_logs_unreachable_workspace_as_warning(
    skill_dirs, mock_skill_repo
):
    active_dir = MagicMock()
    active_dir.mkdir.side_effect = OSError(
        107, "Transport endpoint is not connected"
    )

    with patch(
        "agentclaw.community.core.skill_center.services.skill_service.logger"
    ) as mock_logger:
        _build_service(
            active_dir=active_dir,
            skill_dirs=skill_dirs,
            mock_skill_repo=mock_skill_repo,
        )

    mock_logger.error.assert_not_called()
    mock_logger.warning.assert_called_once()
    assert "Transport endpoint is not connected" in mock_logger.warning.call_args.args[0]
    active_dir.mkdir.assert_called_once_with(parents=True, exist_ok=True)
