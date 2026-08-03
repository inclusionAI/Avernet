"""First-upload lifecycle for a Bot-owned Local Skill.

The service owns validation and compensation; the HTTP adapter only translates
the raw request body and maps the public response.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any
from uuid import uuid4

from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import CollaboratorServiceProtocol
from agentclaw.community.core.bot_collaborator.repository.protocol import BotCollabLogRepositoryProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillInvalidPackageError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.skill_center.services.repositories import (
    SkillRepository,
    SkillSetRepository,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.skill_center.factories import SkillServiceFactory
from agentclaw.community.core.skill_center.factories import SkillSetServiceFactory
from agentclaw.community.core.skill_center.local_skill_cleanup import (
    LocalSkillCleanupRepository,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditGuard,
    SkillsPoolEditPausedError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope

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
        skill_set_service_factory: SkillSetServiceFactory,
        audit_log_repo: BotCollabLogRepositoryProtocol,
        edit_guard: SkillsPoolEditGuard,
        cleanup_repo: LocalSkillCleanupRepository,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory
        self._skill_set_service_factory = skill_set_service_factory
        self._audit_log_repo = audit_log_repo
        self._edit_guard = edit_guard
        self._cleanup_repo = cleanup_repo

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        # The pre-lock read exists only to address the distributed lock.  Every
        # authoritative decision is deliberately repeated after acquisition.
        initial_bot = self._authorize(bot_id, owner_id, actor_id)
        scope = self._scope_for(initial_bot, bot_id)
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditPausedError as exc:
            from agentclaw.community.core.skill_center.errors import LocalSkillEditPausedError
            raise LocalSkillEditPausedError() from exc
        try:
            bot = self._authorize(bot_id, owner_id, actor_id)
            if self._scope_for(bot, bot_id) != scope:
                from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            name, description, files = self._unpack(package)
            # Re-read same-name candidates, owner, readiness and default state
            # under the edit lock.  Uploader identity is intentionally absent.
            default_set = self._skill_set_repo.get_default(
                user_id=owner_id, bolt_id=bot_id, engine_type=bot.get("active_engine")
            )
            if default_set is None:
                from agentclaw.community.core.skill_center.errors import LocalSkillNotFoundError
                raise LocalSkillNotFoundError()
            matches = self._same_name_matches(bot_id=bot_id, owner_id=owner_id, name=name)
            if len(matches) > 1:
                raise LocalSkillDuplicateError()
            if matches:
                return await self._replace(
                    skill=matches[0], bot=bot, owner_id=owner_id, bot_id=bot_id,
                    actor_id=actor_id, name=name, description=description, files=files,
                )
            return await self._create(
                bot=bot, owner_id=owner_id, bot_id=bot_id, actor_id=actor_id,
                name=name, description=description, files=files, default_set=default_set,
            )
        finally:
            self._edit_guard.release(lease)

    async def _create(
        self, *, bot: dict[str, Any], owner_id: str, bot_id: str, actor_id: str,
        name: str, description: str, files: list[tuple[str, bytes]], default_set: dict[str, Any],
    ) -> dict[str, Any]:
        directory, storage = self._skill_service_factory.local_skill_package_storage(
            owner_id=owner_id, bot_id=bot_id, engine_type=bot.get("active_engine"), name=name
        )
        skill: dict[str, Any] | None = None
        associated = False
        excluded = False
        try:
            # A previous failed first upload has no authoritative record, but
            # must not be mixed into this package on a retry.
            await storage.prepare()
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
                user_id=owner_id,
                bolt_id=bot_id,
                engine_type=bot.get("active_engine"),
            )
            if default_set is None or not self._skill_set_repo.add_skill_to_set(
                default_set["id"], skill["id"], user_id=owner_id
            ):
                raise RuntimeError("default Skill Set association failed")
            associated = True
            if not self._skill_set_repo.add_default_skill_exclusion(
                owner_id, bot_id, int(default_set["id"]), int(skill["id"])
            ):
                raise RuntimeError("default Skill Set exclusion failed")
            excluded = True
            self._audit_log_repo.insert(
                {"bot_id": bot_id, "owner_id": owner_id, "operator_id": actor_id,
                 "detail": json.dumps({"action": "local_skill_upload", "skill_id": skill["id"]})}
            )
            return {
                "operation": "created",
                "skill": {**skill, "active": False},
                "actor_id": actor_id,
            }
        except (LocalSkillInvalidPackageError, LocalSkillDuplicateError):
            raise
        except Exception as exc:  # details remain internal; public mapper is fixed
            # Compensation must continue after a failed rollback step: a failed
            # association delete must never prevent package cleanup.
            if excluded and skill is not None:
                try:
                    self._skill_set_repo.remove_default_skill_exclusion(
                        owner_id, bot_id, int(default_set["id"]), int(skill["id"])
                    )
                except Exception:
                    pass
            if associated and skill is not None:
                try:
                    self._skill_set_repo.remove_skill_from_set(default_set["id"], skill["id"])
                except Exception:
                    pass
            if skill is not None:
                try:
                    self._skill_repo.delete(skill["id"])
                except Exception:
                    pass
            try:
                await storage.cleanup()
            except Exception:
                pass
            raise LocalSkillStorageError() from exc

    async def _replace(
        self, *, skill: dict[str, Any], bot: dict[str, Any], owner_id: str, bot_id: str,
        actor_id: str, name: str, description: str, files: list[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """Stage a complete replacement, then atomically switch its locator authority."""
        old_locator = str(skill["git_path"])[len("local://"):]
        version_dir = f".{name}.replacement-{uuid4().hex}"
        new_locator, staged = self._skill_service_factory.local_skill_package_storage(
            owner_id=owner_id, bot_id=bot_id, engine_type=bot.get("active_engine"),
            name=name, directory_name=version_dir,
        )
        old_storage = self._skill_service_factory.local_skill_package_storage_for_locator(
            owner_id=owner_id, bot_id=bot_id, engine_type=bot.get("active_engine"), locator=old_locator,
        )
        old_metadata = {
            "description": skill.get("description"), "git_path": skill.get("git_path"),
            "user_id": skill.get("user_id"),
        }
        switched = False
        try:
            await staged.write(files)
            updated = self._skill_repo.update(
                skill["id"],
                {"description": description, "git_path": f"local://{new_locator}", "user_id": owner_id},
            )
            if updated is None:
                raise RuntimeError("Local Skill metadata switch failed")
            switched = True
            if bool(skill["active"]) and not self._sync_runtime(bot, owner_id, bot_id):
                raise LocalSkillRuntimeSyncError()
            self._audit_log_repo.insert(
                {"bot_id": bot_id, "owner_id": owner_id, "operator_id": actor_id,
                 "detail": json.dumps({"action": "local_skill_replace", "skill_id": skill["id"]})}
            )
        except LocalSkillRuntimeSyncError:
            await self._restore_replacement(
                skill=skill, old_metadata=old_metadata, bot=bot, owner_id=owner_id,
                bot_id=bot_id, staged=staged, staged_locator=new_locator, switched=switched,
            )
            raise
        except Exception as exc:
            if switched:
                await self._restore_replacement(
                    skill=skill, old_metadata=old_metadata, bot=bot, owner_id=owner_id,
                    bot_id=bot_id, staged=staged, staged_locator=new_locator, switched=True,
                )
            else:
                await self._discard_or_record(
                    bot=bot, owner_id=owner_id, bot_id=bot_id, skill_id=str(skill["id"]),
                    storage=staged, locator=new_locator,
                )
            raise LocalSkillStorageError() from exc
        # The new authority is committed.  Old bytes are now garbage, never a
        # reason to undo a working replacement.  A failed purge is durable work.
        try:
            cleaned = await old_storage.cleanup()
        except Exception:
            cleaned = False
        if not cleaned:
            if not self._record_cleanup(bot, owner_id, bot_id, str(skill["id"]), old_locator):
                raise LocalSkillStorageError()
        return {
            "operation": "updated",
            "skill": {**skill, "description": description, "git_path": f"local://{new_locator}", "user_id": owner_id},
            "actor_id": actor_id,
        }

    async def _restore_replacement(
        self, *, skill: dict[str, Any], old_metadata: dict[str, Any], bot: dict[str, Any],
        owner_id: str, bot_id: str, staged, staged_locator: str, switched: bool,
    ) -> None:
        if switched:
            restored = self._skill_repo.update(skill["id"], old_metadata)
            if restored is None:
                raise LocalSkillStorageError()
            if bool(skill["active"]) and not self._sync_runtime(bot, owner_id, bot_id):
                raise LocalSkillRuntimeSyncError()
        await self._discard_or_record(
            bot=bot, owner_id=owner_id, bot_id=bot_id, skill_id=str(skill["id"]),
            storage=staged, locator=staged_locator,
        )

    async def _discard_or_record(self, *, bot: dict[str, Any], owner_id: str, bot_id: str, skill_id: str, storage, locator: str) -> None:
        try:
            if await storage.cleanup():
                return
        except Exception:
            pass
        if not self._record_cleanup(bot, owner_id, bot_id, skill_id, locator):
            raise LocalSkillStorageError()

    def _record_cleanup(self, bot: dict[str, Any], owner_id: str, bot_id: str, skill_id: str, locator: str) -> bool:
        return bool(self._cleanup_repo.record_pending(
            env=str(bot["env"]), owner_id=owner_id, bot_id=bot_id,
            skill_id=skill_id, package_locator=locator,
        ))

    def _same_name_matches(self, *, bot_id: str, owner_id: str, name: str) -> list[dict[str, Any]]:
        rows = self._skill_repo.list_bot_local_by_name(bot_id=bot_id, name=name)
        if bot_id != "default":
            return rows
        unknown = [row for row in rows if not row.get("user_id")]
        if unknown:
            from agentclaw.community.core.skill_center.errors import LocalSkillOwnerAmbiguousError
            raise LocalSkillOwnerAmbiguousError()
        return [row for row in rows if str(row.get("user_id")) == owner_id]

    def _sync_runtime(self, bot: dict[str, Any], owner_id: str, bot_id: str) -> bool:
        try:
            service = self._skill_set_service_factory.create(
                user_id=owner_id, entity_id=owner_id, bot_id=bot_id,
                engine_type=bot.get("active_engine"), entity_type=bot.get("entity_type"),
            )
            return bool(service.sync_runtime())
        except Exception:
            return False

    @staticmethod
    def _scope_for(bot: dict[str, Any], bot_id: str) -> BotSkillLayoutScope:
        return BotSkillLayoutScope(env=str(bot["env"]), entity_id=str(bot["entity_id"]), bot_id=bot_id)

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
                or re.match(r"^[A-Za-z]:", path) is not None
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
            try:
                files.append((path, archive.read(info)))
            except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
                raise LocalSkillInvalidPackageError() from exc
        skill_files = [item for item in files if item[0].split("/")[-1] == "SKILL.md"]
        if len(skill_files) != 1:
            raise LocalSkillInvalidPackageError()
        skill_path, markdown = skill_files[0]
        roots = {path.split("/")[0] for path, _ in files}
        wrapper = skill_path.split("/")[0] if "/" in skill_path else None
        if wrapper is not None and len(roots) != 1:
            raise LocalSkillInvalidPackageError()
        try:
            text = markdown.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise LocalSkillInvalidPackageError() from exc
        name_match = re.search(r"(?m)^name:\s*([^\n]+)\s*$", text)
        desc_match = re.search(r"(?m)^description:\s*([^\n]+)\s*$", text)
        if not name_match or not desc_match:
            raise LocalSkillInvalidPackageError()
        name, description = name_match.group(1).strip(' "\''), desc_match.group(1).strip(' "\'')
        if not name or not description or not _NAME.fullmatch(name) or name.lower() in {"skills-local", "skills-repo"}:
            raise LocalSkillInvalidPackageError()
        if wrapper and wrapper != name:
            raise LocalSkillInvalidPackageError()
        if wrapper is not None and any(
            not path.startswith(f"{wrapper}/") for path, _ in files
        ):
            raise LocalSkillInvalidPackageError()
        normalized = [(p[len(wrapper) + 1:] if wrapper else p, c) for p, c in files]
        return name, description, normalized
