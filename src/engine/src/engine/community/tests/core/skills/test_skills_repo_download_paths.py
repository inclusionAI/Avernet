"""Test that skill_repo_download module-level path constants follow workspace_root()."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch


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
    with patch.dict(os.environ, {"OPENCLAW_WORKSPACE_DIR": "/tmp/per-bot-X/openclaw/workspace"}):
        mod = _reload_skills_repo_download()
    assert mod.TARGET_DIR == Path("/tmp/per-bot-X/openclaw/workspace/skills/skills-repo")
    assert mod.BACKUP_DIR == Path("/tmp/per-bot-X/openclaw/workspace/skills/.skills-repo-backups")
    assert mod.ETAG_FILE  == Path("/tmp/per-bot-X/openclaw/workspace/skills/.skills-repo-etag")


def test_paths_fallback_to_home_when_unset():
    """env 未设 → fallback 到 Path.home()/.openclaw/workspace/skills/... (改造前一字不差)"""
    env_without = {k: v for k, v in os.environ.items() if k != "OPENCLAW_WORKSPACE_DIR"}
    with patch.dict(os.environ, env_without, clear=True):
        mod = _reload_skills_repo_download()
    home_skills = Path.home() / ".openclaw" / "workspace" / "skills"
    assert mod.TARGET_DIR == home_skills / "skills-repo"
    assert mod.BACKUP_DIR == home_skills / ".skills-repo-backups"
    assert mod.ETAG_FILE  == home_skills / ".skills-repo-etag"
