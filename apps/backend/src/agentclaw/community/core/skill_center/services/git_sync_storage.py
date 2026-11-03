"""Bootstrap/archive and object-storage IO used by GitSync."""
from __future__ import annotations

import json
import os
import requests
import subprocess
from pathlib import Path

from agentclaw.community.log import get_logger

logger = get_logger()

class GitSyncStorageIO:
    async def _check_zip_exists(self) -> bool:
        """Check if archive zip exists on NAS."""
        return await self._run_sync(self._sync_check_zip_exists)

    def _sync_check_zip_exists(self) -> bool:
        return self.config.archive_zip.exists()

    async def _download_and_extract_zip(self):
        """Download zip from NAS and extract to local bare repo."""
        return await self._run_sync(self._sync_download_and_extract_zip)

    def _sync_download_and_extract_zip(self):
        zip_path = self.config.archive_zip
        # Use home directory instead of /tmp (permission issues)
        tmp_zip = Path.home() / "aiworkbench_bootstrap.tar.gz"
        target_dir = self.config.local_bare_repo

        # Copy tar to local tmp (rsync preserves modification time)
        logger.info(f"[GitSyncService] Downloading {zip_path} to {tmp_zip}")
        result = subprocess.run(
            ["rsync", "-av", str(zip_path), str(tmp_zip)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to download archive: {result.stderr}")

        # Extract with tar (standard Linux tool)
        logger.info(f"[GitSyncService] Extracting {tmp_zip} to {target_dir}")
        target_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["tar", "-xzf", str(tmp_zip), "-C", str(target_dir)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Failed to extract archive: {result.stderr}")

        # Cleanup
        tmp_zip.unlink(missing_ok=True)
        logger.info(f"[GitSyncService] Bootstrap from tar complete: {target_dir}")

    async def _clone_bare_repo(self):
        """Clone repository as bare."""
        return await self._run_sync(self._sync_clone_bare_repo)

    def _sync_clone_bare_repo(self):
        target = self.config.local_bare_repo
        target.parent.mkdir(parents=True, exist_ok=True)

        logger.info(f"[GitSyncService] Cloning {self._repo_url} to {target}")
        result = subprocess.run(
            ["git", "clone", "--bare", "--branch", self.config.branch,
             self._repo_url, str(target)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Git clone failed: {result.stderr}")

        logger.info(f"[GitSyncService] Bare clone complete: {target}")

    async def _download_from_oss_and_extract(self):
        """Download tar.gz from OSS and extract to local bare repo (fallback)."""
        return await self._run_sync(self._sync_download_from_oss_and_extract)

    def _sync_download_from_oss_and_extract(self):
        if self._oss_storage is None:
            raise RuntimeError("OSS storage not configured, cannot fallback")

        oss_path = self.config.oss_archive_path
        target_dir = self.config.local_bare_repo
        if target_dir.exists():
            raise RuntimeError(
                f"OSS fallback target already exists at {target_dir}; "
                "refusing to overwrite"
            )

        # Get presigned download URL
        url = self._oss_storage.sign_url(oss_path, expires=3600)
        if not url:
            raise RuntimeError(f"Failed to sign URL for {oss_path}")

        tmp_zip = Path.home() / f"aiworkbench_fallback_{os.getpid()}.tar.gz"

        try:
            # Download via HTTP
            logger.info(f"[GitSyncService] Fallback: downloading tar from OSS: {oss_path}")
            resp = requests.get(url, timeout=300, stream=True)
            resp.raise_for_status()
            with tmp_zip.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            # Extract
            logger.info(f"[GitSyncService] Fallback: extracting {tmp_zip} to {target_dir}")
            target_dir.mkdir(parents=True, exist_ok=True)
            result = subprocess.run(
                ["tar", "-xzf", str(tmp_zip), "-C", str(target_dir)],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to extract archive: {result.stderr}")

            # Validate the extracted directory is a valid bare repo
            head_file = target_dir / "HEAD"
            if not head_file.exists():
                raise RuntimeError(f"Extracted archive is not a valid bare repo: {target_dir}")

            result = subprocess.run(
                ["git", "fsck", "--full"],
                cwd=target_dir,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(f"Git fsck failed on extracted archive: {result.stderr}")

            logger.info(f"[GitSyncService] Fallback: bootstrap from OSS tar complete: {target_dir}")
        finally:
            tmp_zip.unlink(missing_ok=True)

    async def _try_create_zip_for_others(self):
        """First service creates zip for cluster (best effort)."""
        try:
            cache = self._cache_plugin
            lock_key = "create_aiworkbench_zip"
            lock_value = cache.acquire_lock(lock_key, ttl=600)

            if not lock_value:
                logger.info("[GitSyncService] Zip creation: lock held by other service")
                return

            try:
                # Double check
                if not self.config.archive_zip.exists():
                    logger.info("[GitSyncService] Creating aiworkbench.tar.gz for cluster...")
                    await self._create_and_upload_zip()
                    logger.info("[GitSyncService] Tar created and uploaded")
            finally:
                cache.release_lock(lock_key, lock_value)

        except Exception as e:
            logger.warning(f"[GitSyncService] Tar creation best-effort failed: {e}")

    async def _create_and_upload_zip(self):
        """Create zip from local bare repo and upload to NAS."""
        return await self._run_sync(self._sync_create_and_upload_zip)

    def _sync_create_and_upload_zip(self):
        bare_dir = self.config.local_bare_repo
        # Use home directory instead of /tmp (permission issues)
        tar_output = Path.home() / f"aiworkbench_{os.getpid()}.tar.gz"
        nas_path = self.config.archive_zip

        # Create tar.gz (using standard Linux tar)
        logger.info(f"[GitSyncService] Creating tar.gz {bare_dir} to {tar_output}")
        result = subprocess.run(
            ["tar", "-czf", str(tar_output), "."],
            cwd=bare_dir,
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Tar creation failed: {result.stderr}")

        # Upload to NAS/OSS
        logger.info(f"[GitSyncService] Uploading tar.gz to {nas_path}")
        result = subprocess.run(
            ["rsync", "-av", "--inplace", str(tar_output), str(nas_path)],
            capture_output=True,
            text=True
        )
        if result.returncode != 0:
            raise RuntimeError(f"Tar upload failed: {result.stderr}")

        # Cleanup
        tar_output.unlink(missing_ok=True)

    # ==========================================================================
    # Sync - Periodic synchronization
    # ==========================================================================

    async def _sync_to_oss_async(self):
        """Background sync to OSS (non-blocking)."""
        try:
            await self._run_sync(self._sync_to_oss_sync)
        except Exception as e:
            logger.warning(f"[GitSyncService] OSS sync failed: {e}")

    def _sync_to_oss_sync(self):
        """Sync local disk to OSS (background task)."""
        logger.info("[GitSyncService] Starting background OSS sync...")

        for subtree in self.config.subtrees:
            name = subtree["name"]
            local_dir = subtree["target_dir"]
            oss_dir = subtree.get("oss_dir")

            if not oss_dir:
                continue

            try:
                oss_dir.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(
                    ["rsync", "-rltD", "--delete",
                     "--exclude=.git", "--exclude=.gitignore",
                     "--exclude=.fuse_hidden*",  # fuse 删除占用文件，传输中会消失
                     "--exclude=.*.*.*",  # editor temp files
                     str(local_dir) + "/", str(oss_dir) + "/"],
                    capture_output=True,
                    text=True
                )
                if result.returncode == 0:
                    logger.info(f"[GitSyncService] OSS sync complete: {name}")
                elif result.returncode == 24:
                    # rsync exit 24 = "some files vanished before transfer";
                    # expected noise under fuse-mounted OSS, treat as acceptable.
                    logger.warning(
                        f"[GitSyncService] OSS sync {name}: some source files vanished "
                        f"during transfer (rsync 24), treated as acceptable"
                    )
                else:
                    logger.warning(f"[GitSyncService] OSS sync failed for {name}: {result.stderr}")
            except Exception as e:
                logger.warning(f"[GitSyncService] OSS sync error for {name}: {e}")

        logger.info("[GitSyncService] Background OSS sync finished")

    def _sync_upload_skills_repo_to_oss(self) -> None:
        if self._oss_storage is None:
            raise RuntimeError(
                "Skills-repo OSS upload failed: oss_storage plugin not configured"
            )

        source_dir = self.config.skills_target
        if not source_dir.exists():
            raise RuntimeError(f"Skills target directory does not exist: {source_dir}")
        tar_output = Path.home() / f"aiworkbench_{os.getpid()}.tar.gz"

        try:
            # Create tar.gz from skills working directory (not bare repo)
            logger.info(f"[GitSyncService] Creating tar.gz {source_dir} to {tar_output}")
            # text=False: skills-repo 含非 UTF-8 文件名时 tar 会把警告写 stderr，
            # text=True 会在 stdout/stderr 解码处抛 UnicodeDecodeError → 误判 clone 失败。
            # 用 bytes 捕获，仅出错时 replace 解码报错信息。
            result = subprocess.run(
                ["tar", "-czf", str(tar_output), "."],
                cwd=source_dir,
                capture_output=True,
            )
            if result.returncode != 0:
                err = result.stderr.decode("utf-8", errors="replace")
                raise RuntimeError(f"Tar creation failed: {err}")

            # Upload to OSS (for VM download)
            oss_path = self.config.oss_archive_path
            logger.info(f"[GitSyncService] Uploading tar.gz to OSS: {oss_path}")
            ok = self._oss_storage.put_file(oss_path, str(tar_output))
            if not ok:
                raise RuntimeError(f"OSS upload failed: {oss_path}")
            logger.info(f"[GitSyncService] OSS upload complete: {oss_path}")
        finally:
            tar_output.unlink(missing_ok=True)

    def _sync_refresh_meta_to_oss(self) -> None:
        """Refresh the meta JSON with a new presigned URL.

        Called on every sync cycle so the presigned URL in the meta file
        never expires, even when the skills-repo tar.gz itself hasn't changed.

        Raises:
            RuntimeError: If meta JSON upload or ACL setting fails.
        """
        if self._oss_storage is None:
            raise RuntimeError(
                "Meta refresh failed: oss_storage plugin not configured"
            )

        oss_path = self.config.oss_archive_path
        meta_etag = self._oss_storage.get_etag(oss_path)
        if meta_etag is None:
            raise RuntimeError(
                f"Meta refresh failed: tar.gz not yet uploaded ({oss_path})"
            )

        meta_url = self._oss_storage.sign_url(oss_path, expires=7200)
        # sign_url returns an internal endpoint; rewrite to the office-network
        # endpoint so desktop VMs can download without VPN.
        from agentclaw.community.core.skill_center.services.git_sync import _rewrite_presigned_url_to_office
        meta_url = _rewrite_presigned_url_to_office(
            meta_url, self.config.office_oss_endpoint
        )
        meta_content = {
            "url": meta_url,
            "etag": meta_etag,
            "oss_path": oss_path,
            "available": True,
        }
        meta_oss_path = self.config.oss_meta_path
        logger.info(f"[GitSyncService] Refreshing meta JSON: {meta_oss_path}")
        meta_ok = self._oss_storage.put_object(
            meta_oss_path, json.dumps(meta_content, ensure_ascii=False)
        )
        if not meta_ok:
            raise RuntimeError(f"Meta JSON upload failed: {meta_oss_path}")

        acl_ok = self._oss_storage.set_object_acl(meta_oss_path, "public-read")
        if not acl_ok:
            raise RuntimeError(
                f"Meta JSON ACL set to public-read failed: {meta_oss_path}"
            )

        logger.info(
            f"[GitSyncService] Meta JSON refreshed: {meta_oss_path} "
            f"url={meta_url[:120]}... etag={meta_etag}"
        )
