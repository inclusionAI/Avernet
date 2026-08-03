"""First-upload lifecycle for a Bot-owned Local Skill.

The service owns validation and compensation; the HTTP adapter only translates
the raw request body and maps the public response.
"""

from __future__ import annotations

import io
import re
import zipfile
from typing import Any

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import CollaboratorServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillInvalidPackageError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.skill_center.factories import SkillServiceFactory

_MAX_COMPRESSED = 10 * 1024 * 1024
_MAX_EXPANDED = 50 * 1024 * 1024
_MAX_FILE = 10 * 1024 * 1024
_MAX_FILES = 500
_NAME = re.compile(r"^[A-Za-z0-9-]+$")


class LocalSkillUploadService:
    """Authorize, validate, persist and associate an inactive Local Skill."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_service_factory: SkillServiceFactory,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        bot = self._authorize(bot_id, owner_id, actor_id)
        if not is_bot_ready(bot):
            raise LocalSkillNotReadyError()
        name, description, files = self._unpack(package)
        if self._skill_repo.get_bot_local_by_name(
            bot_id=bot_id, name=name, user_id=owner_id
        ) is not None:
            # #725 owns replacement.  Never overwrite a package in this ticket.
            raise LocalSkillDuplicateError()

        directory, storage = self._skill_service_factory.local_skill_package_storage(
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=bot.get("active_engine"),
            name=name,
        )
        skill: dict[str, Any] | None = None
        associated = False
        excluded = False
        try:
            await storage.write(files)
            skill = self._skill_repo.create(
                {
                    "name": name,
                    "description": description,
                    "git_path": f"local://{directory}",
                    "category": "general",
                    "tags": "[]",
                    "is_public": False,
                    "user_id": owner_id,
                    "bolt_id": bot_id,
                    "source_type": "upload",
                }
            )
            default_set = self._skill_set_repo.get_default(
                user_id=owner_id, engine_type=bot.get("active_engine")
            )
            if default_set is None or not self._skill_set_repo.add_skill_to_set(
                default_set["id"], skill["id"], user_id=owner_id
            ):
                raise RuntimeError("default Skill Set association failed")
            associated = True
            if not self._skill_repo.add_default_skill_exclusion(
                owner_id, bot_id, int(default_set["id"]), int(skill["id"])
            ):
                raise RuntimeError("default Skill Set exclusion failed")
            excluded = True
            return {
                "operation": "created",
                "skill": {**skill, "active": False},
                "actor_id": actor_id,
            }
        except (LocalSkillInvalidPackageError, LocalSkillDuplicateError):
            raise
        except Exception as exc:  # details remain internal; public mapper is fixed
            if excluded and skill is not None:
                self._skill_repo.remove_default_skill_exclusion(
                    owner_id, bot_id, int(default_set["id"]), int(skill["id"])
                )
            if associated and skill is not None:
                self._skill_set_repo.remove_skill_from_set(default_set["id"], skill["id"])
            if skill is not None:
                self._skill_repo.delete(skill["id"])
            if not await storage.cleanup():
                raise LocalSkillStorageError()
            raise LocalSkillStorageError() from exc

    def _authorize(self, bot_id: str, owner_id: str, actor_id: str) -> dict[str, Any]:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
            raise LocalSkillNotFoundError()
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
                raise LocalSkillNotFoundError()
        return bot

    @staticmethod
    def _unpack(package: bytes) -> tuple[str, str, list[tuple[str, bytes]]]:
        if len(package) > _MAX_COMPRESSED:
            raise LocalSkillTooLargeError()
        try:
            archive = zipfile.ZipFile(io.BytesIO(package))
        except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise LocalSkillInvalidPackageError() from exc
        files: list[tuple[str, bytes]] = []
        total = 0
        seen: set[str] = set()
        for info in archive.infolist():
            path = info.filename
            if info.is_dir():
                continue
            file_kind = (info.external_attr >> 16) & 0o170000
            if (
                path.startswith(("/", "\\"))
                or "\\" in path
                or ".." in path.split("/")
                or len(path) > 256
                or file_kind not in (0, 0o100000)
            ):
                raise LocalSkillInvalidPackageError()
            normalized_path = "/".join(
                part for part in path.split("/") if part not in ("", ".")
            )
            if normalized_path in seen:
                raise LocalSkillInvalidPackageError()
            if info.file_size > _MAX_FILE:
                raise LocalSkillTooLargeError()
            seen.add(normalized_path)
            total += info.file_size
            if len(seen) > _MAX_FILES or total > _MAX_EXPANDED:
                raise LocalSkillTooLargeError()
            files.append((path, archive.read(info)))
        skill_files = [item for item in files if item[0].split("/")[-1] == "SKILL.md"]
        if len(skill_files) != 1:
            raise LocalSkillInvalidPackageError()
        skill_path, markdown = skill_files[0]
        roots = {path.split("/")[0] for path, _ in files}
        wrapper = skill_path.split("/")[0] if "/" in skill_path else None
        if wrapper is not None and len(roots) != 1:
            raise LocalSkillInvalidPackageError()
        if wrapper is None and any("/" in path for path, _ in files):
            raise LocalSkillInvalidPackageError()
        text = markdown.decode("utf-8", errors="strict")
        name_match = re.search(r"(?m)^name:\s*([^\n]+)\s*$", text)
        desc_match = re.search(r"(?m)^description:\s*([^\n]+)\s*$", text)
        if not name_match or not desc_match:
            raise LocalSkillInvalidPackageError()
        name, description = name_match.group(1).strip(' "\''), desc_match.group(1).strip(' "\'')
        if not name or not description or not _NAME.fullmatch(name) or name.lower() in {"skills-local", "skills-repo"}:
            raise LocalSkillInvalidPackageError()
        if wrapper and wrapper != name:
            raise LocalSkillInvalidPackageError()
        normalized = [(p[len(wrapper) + 1:] if wrapper else p, c) for p, c in files]
        return name, description, normalized
