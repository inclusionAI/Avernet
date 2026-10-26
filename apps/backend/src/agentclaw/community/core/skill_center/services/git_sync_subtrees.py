"""Git object/subtree IO used by the GitSync orchestrator."""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import Any

from agentclaw.community.log import get_logger

logger = get_logger()

class GitSyncSubtreeIO:
    async def _git_fetch(self) -> dict[str, Any]:
        """Fetch latest from remote (shallow).

        Self-heal: if the bare repo is missing (e.g. pod rebuilt, disk lost),
        trigger a bootstrap to rebuild it before fetching, instead of failing
        forever. sync_bootstrap() is concurrency-safe via its distributed lock.
        """
        if not self.config.local_bare_repo.exists():
            logger.warning(
                "[GitSyncService] _git_fetch: bare repo missing, triggering self-heal bootstrap"
            )
            bootstrap_result = await self.sync_bootstrap()
            if not bootstrap_result.get("success") or not self.config.local_bare_repo.exists():
                logger.error(
                    "[GitSyncService] _git_fetch: self-heal bootstrap failed (result=%s)",
                    bootstrap_result,
                )
                return {"success": False, "error": "self-heal bootstrap failed"}
            logger.info("[GitSyncService] _git_fetch: self-heal bootstrap succeeded, retrying fetch")
        return await self._run_sync(self._sync_git_fetch)

    def _sync_git_fetch(self) -> dict[str, Any]:
        bare_repo = self.config.local_bare_repo

        if not bare_repo.exists():
            return {"success": False, "error": "Bare repo not found, run bootstrap first"}

        # Use --force to handle non-fast-forward cases (e.g., remote was force-pushed)
        result = subprocess.run(
            ["git", "fetch", self.config.remote_name,
             f"{self.config.branch}:{self.config.branch}", "--depth=1", "--force"],
            cwd=bare_repo,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        # Get FETCH_HEAD
        fetch_head_file = bare_repo / "FETCH_HEAD"
        fetch_head = None
        if fetch_head_file.exists():
            content = fetch_head_file.read_text().strip()
            if content:
                fetch_head = content.split()[0]

        return {"success": True, "fetch_head": fetch_head}

    async def _sync_subtree(self, subtree: dict[str, Any]) -> dict[str, Any]:
        """Sync a single subtree (skills or agents)."""
        return await self._run_sync(self._sync_sync_subtree, subtree)

    def _sync_sync_subtree(self, subtree: dict[str, Any]) -> dict[str, Any]:
        name = subtree["name"]
        source_path = subtree["source_path"]
        target_dir = subtree["target_dir"]
        version_file = target_dir / subtree["version_file"]

        bare_repo = self.config.local_bare_repo

        # Get remote SHA for this subtree
        fetch_head_file = bare_repo / "FETCH_HEAD"
        if not fetch_head_file.exists():
            return {"success": False, "error": "FETCH_HEAD not found", "updated": False}

        # Compute SHA for subtree
        result = subprocess.run(
            ["git", "rev-parse", f"FETCH_HEAD:{source_path}"],
            cwd=bare_repo,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            return {"success": False, "error": f"Cannot resolve {source_path}", "updated": False}

        remote_sha = result.stdout.strip()

        # Check local version
        local_sha = None
        if version_file.exists():
            with open(version_file) as f:
                local_sha = f.read().strip()

        # Compare versions
        if local_sha == remote_sha:
            logger.info(f"[GitSyncService] Subtree {name}: up to date ({remote_sha[:8]})")
            return {"success": True, "updated": False, "sha": remote_sha, "renames": {}}

        logger.info(f"[GitSyncService] Subtree {name}: updating {local_sha[:8] if local_sha else 'none'} -> {remote_sha[:8]}")
        renames = {}
        if name == "skills" and local_sha:
            renames = self._collect_skill_renames(source_path, local_sha, remote_sha)
            if renames:
                logger.info(
                    "[GitSyncService] Subtree %s: detected %d skill renames",
                    name, len(renames),
                )

        # Extract to temp cache
        from agentclaw.community.core.skill_center.services.git_sync import _resolve_temp_cache_base
        cache_dir = _resolve_temp_cache_base() / f"{name}-cache-{os.getpid()}"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)

        try:
            # git archive | tar
            archive_result = subprocess.run(
                ["git", "archive", f"FETCH_HEAD:{source_path}"],
                cwd=bare_repo,
                capture_output=True
            )
            if archive_result.returncode != 0:
                return {"success": False, "error": f"Git archive failed: {archive_result.stderr}", "updated": False}

            # Note: git archive FETCH_HEAD:skills already returns contents without 'skills/' prefix,
            # so we should NOT use --strip-components=1, which would incorrectly remove the first
            # level directory (business/, infra/, third-party/, etc.)
            tar_result = subprocess.run(
                ["tar", "-xf", "-"],
                input=archive_result.stdout,
                cwd=cache_dir,
                capture_output=True
            )
            if tar_result.returncode != 0:
                return {"success": False, "error": f"Tar extraction failed: {tar_result.stderr}", "updated": False}

            # rsync to target
            target_dir.mkdir(parents=True, exist_ok=True)
            # 不带 text=True：-av 会把文件名列到 stdout，repo 含非 UTF-8 文件名时
            # text 解码会抛 UnicodeDecodeError → 误判 subtree/clone 失败。用 bytes。
            rsync_result = subprocess.run(
                ["rsync", "-av", "--delete", "--exclude=.git", "--exclude=.gitignore",
                 "--exclude=.*.*.*",  # Exclude editor temp files (e.g. .file.md.xxxxx)
                 str(cache_dir) + "/", str(target_dir) + "/"],
                capture_output=True,
            )
            if rsync_result.returncode != 0:
                err = rsync_result.stderr.decode("utf-8", errors="replace")
                return {"success": False, "error": f"Rsync failed: {err}", "updated": False}

            # Write version file
            with open(version_file, "w") as f:
                f.write(remote_sha)

            logger.info(f"[GitSyncService] Subtree {name}: synced successfully")
            return {"success": True, "updated": True, "sha": remote_sha, "renames": renames}

        finally:
            # Cleanup cache
            if cache_dir.exists():
                shutil.rmtree(cache_dir)

    def _collect_skill_renames(
        self,
        source_path: str,
        old_tree_sha: str,
        new_tree_sha: str,
    ) -> dict[str, str]:
        """Return git:// old->new paths for SKILL.md renames in a subtree."""
        result = subprocess.run(
            [
                "git", "diff", "--name-status", "-M", "--diff-filter=R",
                old_tree_sha, new_tree_sha, "--",
            ],
            cwd=self.config.local_bare_repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Git rename detection failed for {source_path}: {result.stderr}"
            )

        renames: dict[str, str] = {}
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            status, old_file, new_file = parts
            if not status.startswith("R"):
                continue
            old_skill_dir = self._skill_dir_from_diff_path(old_file)
            new_skill_dir = self._skill_dir_from_diff_path(new_file)
            if not old_skill_dir or not new_skill_dir:
                continue
            renames[f"git://{old_skill_dir}"] = f"git://{new_skill_dir}"
        return renames

    @staticmethod
    def _skill_dir_from_diff_path(path: str) -> str | None:
        normalized = path.strip("/")
        if normalized == "SKILL.md" or not normalized.endswith("/SKILL.md"):
            return None
        return normalized[: -len("/SKILL.md")]

