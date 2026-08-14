"""Test that skill_repo_download module-level path constants follow workspace_root()."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


def _reload_skills_repo_download():
    """Force module reload so module-level constants pick up current env."""
    mod_name = "engine.community.core.skills.skills_repo_download"
    if mod_name in sys.modules:
        importlib.reload(sys.modules[mod_name])
    else:
        importlib.import_module(mod_name)
    return sys.modules[mod_name]


def test_paths_use_env_root_when_set():
    """env 设了 → TARGET_DIR / BACKUP_DIR / ETAG_FILE 都含 env path"""
    with patch.dict(
        os.environ, {"OPENCLAW_WORKSPACE_DIR": "/tmp/per-bot-X/openclaw/workspace"}
    ):
        mod = _reload_skills_repo_download()
    pool_root = Path("/tmp/per-bot-X/openclaw/workspace/skills-pool")
    assert mod.TARGET_DIR == pool_root / "skills-repo"
    assert mod.BACKUP_DIR == pool_root / ".skills-repo-backups"
    assert mod.ETAG_FILE == pool_root / ".skills-repo-etag"
    assert mod.LEGACY_TARGET_DIR == Path(
        "/tmp/per-bot-X/openclaw/workspace/skills/skills-repo"
    )


def test_paths_fallback_to_home_when_unset():
    """env 未设 → fallback 到 Path.home()/.openclaw/workspace/skills/... (改造前一字不差)"""
    env_without = {k: v for k, v in os.environ.items() if k != "OPENCLAW_WORKSPACE_DIR"}
    with patch.dict(os.environ, env_without, clear=True):
        mod = _reload_skills_repo_download()
    workspace = Path.home() / ".openclaw" / "workspace"
    pool_root = workspace / "skills-pool"
    assert mod.TARGET_DIR == pool_root / "skills-repo"
    assert mod.BACKUP_DIR == pool_root / ".skills-repo-backups"
    assert mod.ETAG_FILE == pool_root / ".skills-repo-etag"
    assert mod.LEGACY_TARGET_DIR == workspace / "skills/skills-repo"


@pytest.mark.parametrize(
    ("engine", "expected_pool_root"),
    [
        ("openclaw", ".openclaw/workspace/skills-pool"),
        ("claude_code", ".claude_code/workspace/skills-pool"),
        ("hermes", ".hermes/workspace/skills-pool"),
    ],
)
def test_download_paths_follow_each_engine_planner(
    tmp_path: Path,
    engine: str,
    expected_pool_root: str,
) -> None:
    mod = _reload_skills_repo_download()
    home = tmp_path / "home/admin"
    openclaw_workspace = home / ".openclaw/workspace"

    target, backup, etag, legacy = mod._repo_download_paths(
        engine=engine,
        home=home,
        openclaw_workspace=openclaw_workspace,
    )

    pool_root = home / expected_pool_root
    assert target == pool_root / "skills-repo"
    assert backup == pool_root / ".skills-repo-backups"
    assert etag == pool_root / ".skills-repo-etag"
    assert legacy == openclaw_workspace / "skills/skills-repo"
