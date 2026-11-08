"""Existing GitSync reconciles immutable experts on bootstrap and unchanged fetch."""

import asyncio
import subprocess
import shutil
import pytest

from pathlib import Path
from unittest.mock import MagicMock

from agentclaw.community.core.expert_market.models import ExpertScope
from agentclaw.community.core.expert_market.scanner import ExpertScanner
from agentclaw.community.core.skill_center.services.git_sync import (
    GitSyncConfig,
    GitSyncService,
)
from agentclaw.community.plugins.local.cache import MemoryCachePlugin
from tests.community.core.expert_market.test_catalog import catalog, write_expert  # noqa: F401


def test_real_git_bootstrap_unchanged_sync_and_update(catalog, tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_SYNC_TMP_BASE", str(tmp_path / "extract"))
    root = tmp_path / "source"
    root.mkdir()
    write_expert(root)
    (root / "skills").mkdir()
    (root / "skills" / "README.md").write_text(
        "Existing Skill synchronization stays independent."
    )

    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=root, check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-b", "master")
    git("add", ".")
    git(
        "-c",
        "user.name=Expert test",
        "-c",
        "user.email=expert@example.test",
        "commit",
        "-m",
        "initial",
    )
    first = git("rev-parse", "HEAD")
    config = GitSyncConfig()
    config.local_bare_repo = tmp_path / "cloned.git"
    config.skills_target = tmp_path / "runtime" / "skills-repo"
    config.agents_target = tmp_path / "runtime" / "agents-repo"
    config.enable_oss_sync = False
    for subtree in config.subtrees:
        subtree["target_dir"] = (
            config.skills_target
            if subtree["name"] == "skills"
            else config.agents_target
        )
    resolver = MagicMock()
    resolver.get_secret.return_value = MagicMock(secret_value=str(root))
    skill_service = MagicMock()
    skill_service.sync_skills_from_git.return_value = {"updated": 0, "failed": 0}
    skill_service._refresh_market_cache.return_value = {"cache_refreshed": True}
    factory = MagicMock()
    factory.create.return_value = skill_service
    service = GitSyncService(
        MemoryCachePlugin(),
        factory,
        config,
        MagicMock(),
        resolver,
        repo_url_secret_name="fixture",
        expert_scanner=ExpertScanner(catalog),
    )
    scope = ExpertScope("teamclaw", "dev", "aiworkbench")
    try:
        assert asyncio.run(service.sync_bootstrap())["success"]
        initial = catalog.get(scope, "writer")
        assert initial["source_commit"] == first
        # Losing a directory entry after a successful Git export must heal even
        # when both subtree versions are unchanged.
        with catalog._db.orm_session() as db:
            from agentclaw.community.core.expert_market.orm import ExpertModel

            db.query(ExpertModel).delete()
        result = asyncio.run(service.sync())
        assert result["success"], result
        assert catalog.get(scope, "writer")["source_commit"] == first
        (root / "agents/writer/AGENTS.md").write_text("updated from Git")
        git("add", ".")
        git(
            "-c",
            "user.name=Expert test",
            "-c",
            "user.email=expert@example.test",
            "commit",
            "-m",
            "update",
        )
        result = asyncio.run(service.sync())
        assert result["success"], result
        assert (
            catalog.get(scope, "writer")["identity"][0]["content"] == "updated from Git"
        )
        (root / "agents/writer/expert.yaml").write_text("malformed: true")
        git("add", ".")
        git(
            "-c",
            "user.name=Expert test",
            "-c",
            "user.email=expert@example.test",
            "commit",
            "-m",
            "bad input",
        )
        assert asyncio.run(service.sync())["success"]
        assert (
            catalog.get(scope, "writer")["identity"][0]["content"] == "updated from Git"
        )
        # Replacing the agents tree with a blob or symlink is a structural
        # failure, not an authoritative empty catalogue.
        shutil.rmtree(root / "agents")
        (root / "agents").write_text("not a tree")
        git("add", "-A")
        git(
            "-c",
            "user.name=Expert test",
            "-c",
            "user.email=expert@example.test",
            "commit",
            "-m",
            "agents blob",
        )
        with pytest.raises(ValueError, match="agents must be a Git tree"):
            ExpertScanner(catalog).scan_git(
                root, source_commit=git("rev-parse", "HEAD"), scope=scope
            )
        assert catalog.get(scope, "writer") is not None
        assert not asyncio.run(service.sync())["success"]
        assert catalog.get(scope, "writer") is not None
        (root / "agents").unlink()
        (root / "agents").symlink_to("skills", target_is_directory=True)
        git("add", "-A")
        git(
            "-c",
            "user.name=Expert test",
            "-c",
            "user.email=expert@example.test",
            "commit",
            "-m",
            "agents symlink",
        )
        with pytest.raises(ValueError, match="agents must be a Git tree"):
            ExpertScanner(catalog).scan_git(
                root, source_commit=git("rev-parse", "HEAD"), scope=scope
            )
        assert catalog.get(scope, "writer") is not None
        assert not asyncio.run(service.sync())["success"]
        assert catalog.get(scope, "writer") is not None
        # Git cannot retain an empty directory. Its absence in a completely
        # enumerated commit means the last expert really was removed.
        (root / "agents").unlink()
        git("add", "-A")
        git(
            "-c",
            "user.name=Expert test",
            "-c",
            "user.email=expert@example.test",
            "commit",
            "-m",
            "remove last expert",
        )
        result = asyncio.run(service.sync())
        assert result["success"], result
        assert catalog.get(scope, "writer") is None
        write_expert(root)
        git("add", "-A")
        git(
            "-c",
            "user.name=Expert test",
            "-c",
            "user.email=expert@example.test",
            "commit",
            "-m",
            "restore expert",
        )
        assert asyncio.run(service.sync())["success"]
        service.config.remote_name = "unavailable-remote"
        assert not asyncio.run(service.sync())["success"]
        assert catalog.get(scope, "writer") is not None
    finally:
        service._executor.shutdown(wait=True)
