"""Shared exact-package publisher for Bot-local Skills."""

from __future__ import annotations

import fcntl
import hashlib
import io
import logging
import os
import re
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import yaml

from engine.community.core.skills.exceptions import (
    LocalSkillPackageInvalidError,
    LocalSkillPackagePublishFailedError,
    LocalSkillPackagePublishInProgressError,
    LocalSkillPackagePublishLockUnavailableError,
    LocalSkillPackageRollbackFailedError,
    LocalSkillPackageTooLargeError,
)
from engine.community.core.skills.models import (
    LocalSkillPackageAction,
    LocalSkillPackageApplyResult,
)

MAX_COMPRESSED_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_FILES = 500
MAX_PATH_LENGTH = 256
_SKILL_NAME = re.compile(r"^[A-Za-z0-9-]+$")
log = logging.getLogger("engine.local_skill_package")


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


class LocalSkillPackagePublisher:
    """Validate, stage and atomically replace one complete package directory."""

    def publish(
        self, *, skill_name: str, package: bytes, target: Path
    ) -> LocalSkillPackageApplyResult:
        entries = self._validate(skill_name=skill_name, package=package)
        digest = "sha256:" + hashlib.sha256(package).hexdigest()
        target = Path(target)
        if target.name != skill_name:
            raise LocalSkillPackageInvalidError("target_name_mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)

        stage = target.parent / f".{skill_name}.apply-{uuid4().hex}"
        backup = target.parent / f".{skill_name}.rollback-{uuid4().hex}"
        try:
            self._write_stage(stage, entries)
            with self._target_lock(target):
                existed = os.path.lexists(target)
                if existed:
                    try:
                        os.replace(target, backup)
                    except OSError as exc:
                        raise LocalSkillPackagePublishFailedError(
                            "unable_to_backup_existing_package"
                        ) from exc
                try:
                    os.replace(stage, target)
                except OSError as exc:
                    if existed:
                        try:
                            os.replace(backup, target)
                        except OSError as rollback_exc:
                            raise LocalSkillPackageRollbackFailedError(
                                "publish_and_rollback_failed"
                            ) from rollback_exc
                    raise LocalSkillPackagePublishFailedError(
                        "unable_to_publish_package"
                    ) from exc

                if existed:
                    try:
                        _remove_path(backup)
                    except OSError as exc:
                        # The new package is already the committed authority.
                        # Cleanup may have partially mutated ``backup`` (for
                        # example an NFS .nfs* unlink failure), so it is no
                        # longer a safe rollback source.  Keep the complete new
                        # target and leave the unique hidden residue available
                        # for an operator/collector to retry.
                        log.warning(
                            "local Skill package committed but old backup cleanup "
                            "failed; target=%s residue=%s error=%s",
                            target,
                            backup,
                            exc,
                        )
        finally:
            _remove_path(stage)

        return LocalSkillPackageApplyResult(
            skill_name=skill_name,
            action=(
                LocalSkillPackageAction.REPLACED
                if existed
                else LocalSkillPackageAction.CREATED
            ),
            content_digest=digest,
            target_path=str(target),
        )

    @staticmethod
    def _write_stage(stage: Path, entries: list[tuple[str, bytes]]) -> None:
        try:
            stage.mkdir()
            for relative_path, content in entries:
                destination = stage / relative_path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        except OSError as exc:
            raise LocalSkillPackagePublishFailedError(
                "unable_to_stage_package"
            ) from exc

    @staticmethod
    @contextmanager
    def _target_lock(target: Path):
        lock_root = Path(tempfile.gettempdir()) / "agentclaw-local-skill-locks"
        lock_name = hashlib.sha256(str(target).encode("utf-8")).hexdigest()
        try:
            lock_root.mkdir(parents=True, exist_ok=True)
            lock_file = (lock_root / f"{lock_name}.lock").open("a+b")
        except OSError as exc:
            raise LocalSkillPackagePublishLockUnavailableError(
                "publish_lock_unavailable"
            ) from exc
        with lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise LocalSkillPackagePublishInProgressError(
                    "publish_in_progress"
                ) from exc
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _validate(*, skill_name: str, package: bytes) -> list[tuple[str, bytes]]:
        if not _SKILL_NAME.fullmatch(skill_name):
            raise LocalSkillPackageInvalidError("invalid_skill_name")
        if len(package) > MAX_COMPRESSED_BYTES:
            raise LocalSkillPackageTooLargeError("compressed_package_too_large")
        try:
            archive = zipfile.ZipFile(io.BytesIO(package))
        except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise LocalSkillPackageInvalidError("invalid_zip") from exc

        entries: list[tuple[str, bytes]] = []
        seen: set[str] = set()
        expanded = 0
        with archive:
            for info in archive.infolist():
                raw = info.filename
                parts = raw.split("/")
                file_kind = (info.external_attr >> 16) & 0o170000
                if (
                    not raw
                    or raw.startswith(("/", "\\"))
                    or "\\" in raw
                    or re.match(r"^[A-Za-z]:", raw)
                    or any(part in {"", ".", ".."} for part in parts)
                    or len(raw) > MAX_PATH_LENGTH
                ):
                    raise LocalSkillPackageInvalidError("unsafe_file_path")
                if info.is_dir():
                    if file_kind not in (0, 0o040000):
                        raise LocalSkillPackageInvalidError("unsafe_file_type")
                    continue
                if file_kind not in (0, 0o100000):
                    raise LocalSkillPackageInvalidError("unsafe_file_type")
                if raw in seen:
                    raise LocalSkillPackageInvalidError("duplicate_file_path")
                if info.file_size > MAX_FILE_BYTES:
                    raise LocalSkillPackageTooLargeError("file_too_large")
                seen.add(raw)
                expanded += info.file_size
                if len(seen) > MAX_FILES or expanded > MAX_EXPANDED_BYTES:
                    raise LocalSkillPackageTooLargeError("expanded_package_too_large")
                try:
                    content = archive.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                    raise LocalSkillPackageInvalidError("unreadable_archive") from exc
                if len(content) != info.file_size:
                    raise LocalSkillPackageInvalidError("unreadable_archive")
                entries.append((raw, content))

        manifests = [content for path, content in entries if path == "SKILL.md"]
        if len(manifests) != 1:
            raise LocalSkillPackageInvalidError("missing_or_multiple_root_manifest")
        try:
            try:
                text = manifests[0].decode("utf-8")
            except UnicodeDecodeError:
                text = manifests[0].decode("gbk")
            text = text.lstrip("\ufeff")
            lines = text.splitlines()
            if lines and lines[0].strip() == "---":
                closing_index = next(
                    (
                        index
                        for index, line in enumerate(lines[1:], start=1)
                        if line.strip() == "---"
                    ),
                    None,
                )
                if closing_index is None:
                    raise ValueError("manifest frontmatter is not closed")
                metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
            else:
                # The existing Legacy upload contract accepts YAML-only
                # manifests. Backend canonicalises the filename before this
                # boundary; Engine still revalidates the canonical bytes.
                metadata = yaml.safe_load(text)
        except (UnicodeDecodeError, ValueError, yaml.YAMLError) as exc:
            raise LocalSkillPackageInvalidError("invalid_manifest") from exc
        if (
            not isinstance(metadata, dict)
            or metadata.get("name") != skill_name
            or not isinstance(metadata.get("description"), str)
            or not metadata["description"].strip()
        ):
            raise LocalSkillPackageInvalidError("skill_name_mismatch")
        return entries


__all__ = [
    "LocalSkillPackageAction",
    "LocalSkillPackageInvalidError",
    "LocalSkillPackagePublisher",
]
