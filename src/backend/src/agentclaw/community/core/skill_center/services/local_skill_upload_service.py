"""Create-or-replace lifecycle for a Bot-owned Local Skill.

The service owns validation and compensation; the HTTP adapter only translates
the raw request body and maps the public response.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any, Callable, TYPE_CHECKING
from uuid import uuid4

import yaml
from injector import inject

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
)
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillInvalidPackageError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
    LocalSkillTooLargeError,
    LocalSkillLayoutRollbackError,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.skill_center.services.skill_parser import SkillParser
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.skill_center.factories import (
    SkillServiceFactory,
    SkillSetServiceFactory,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )

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
        device_context_resolver_provider: Callable[[], "DeviceContextResolver"],
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory
        self._skill_set_service_factory = skill_set_service_factory
        self._audit_log_repo = audit_log_repo
        self._edit_guard = edit_guard
        self._device_context_resolver_provider = device_context_resolver_provider

    async def upload_local_skill(
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        # The pre-lock read exists only to address the distributed lock.  Every
        # authoritative decision is deliberately repeated after acquisition.
        initial_bot = self._authorize(bot_id, owner_id, actor_id)
        scope = self._scope_for(initial_bot, bot_id)
        try:
            lease = await self._edit_guard.acquire_for_edit_wait(scope=scope)
        except SkillsPoolEditBusyError as exc:
            raise LocalSkillEditBusyError() from exc
        except SkillsPoolEditRollbackError as exc:
            raise LocalSkillLayoutRollbackError() from exc
        except SkillsPoolEditLockUnavailableError as exc:
            raise LocalSkillEditLockUnavailableError() from exc
        except SkillsPoolEditPausedError as exc:
            raise LocalSkillEditPausedError() from exc
        try:
            bot = self._authorize(bot_id, owner_id, actor_id)
            if self._scope_for(bot, bot_id) != scope:
                from agentclaw.community.core.skill_center.errors import (
                    LocalSkillNotFoundError,
                )

                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            name, description, files = self._unpack(package)
            is_teclaw = self._is_teclaw(bot_id=bot_id, owner_id=owner_id)
            # Re-read same-name candidates, owner, readiness and default state
            # under the edit lock.  Uploader identity is intentionally absent.
            default_set = self._ensure_default_set(
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
            )
            matches = self._same_name_matches(
                bot_id=bot_id, owner_id=owner_id, name=name
            )
            if len(matches) > 1:
                raise LocalSkillDuplicateError()
            if matches:
                return await self._replace(
                    skill=matches[0],
                    bot=bot,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    actor_id=actor_id,
                    name=name,
                    description=description,
                    files=files,
                    is_teclaw=is_teclaw,
                )
            return await self._create(
                bot=bot,
                owner_id=owner_id,
                bot_id=bot_id,
                actor_id=actor_id,
                name=name,
                description=description,
                files=files,
                default_set=default_set,
                is_teclaw=is_teclaw,
            )
        finally:
            self._edit_guard.release(lease)

    async def _create(
        self,
        *,
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
        actor_id: str,
        name: str,
        description: str,
        files: list[tuple[str, bytes]],
        default_set: dict[str, Any],
        is_teclaw: bool,
    ) -> dict[str, Any]:
        directory, storage = self._skill_service_factory.local_skill_package_storage(
            entity_id=str(bot["entity_id"]),
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=bot.get("active_engine"),
            entity_type=str(bot.get("entity_type") or "staff"),
            is_desktop=bot.get("bot_type") == "desktop",
            is_teclaw=is_teclaw,
            name=name,
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
            default_set = self._ensure_default_set(
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
            )
            if not self._skill_set_repo.add_skill_to_set(
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
                {
                    "bot_id": bot_id,
                    "owner_id": owner_id,
                    "operator_id": actor_id,
                    "detail": json.dumps(
                        {"action": "local_skill_upload", "skill_id": skill["id"]}
                    ),
                }
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
                    self._skill_set_repo.remove_skill_from_set(
                        default_set["id"], skill["id"]
                    )
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

    @staticmethod
    def _scope_for(bot: dict[str, Any], bot_id: str) -> BotSkillLayoutScope:
        return BotSkillLayoutScope(
            env=str(bot["env"]),
            entity_id=str(bot["entity_id"]),
            bot_id=bot_id,
        )

    def _ensure_default_set(
        self, *, owner_id: str, bot_id: str, engine_type: str | None
    ) -> dict[str, Any]:
        default_set = self._skill_set_repo.get_default(
            user_id=owner_id,
            bolt_id=bot_id,
            engine_type=engine_type,
        )
        if default_set is not None:
            return default_set
        return self._skill_set_repo.create(
            {
                "name": "默认技能集",
                "description": "系统默认技能集，用户可以根据需要添加或移除技能",
                "user_id": owner_id,
                "bolt_id": bot_id,
                "is_default": True,
                "is_builtin": False,
                "is_active": False,
                "engine_type": engine_type,
            }
        )

    def _ensure_default_set_membership(
        self,
        *,
        owner_id: str,
        bot_id: str,
        engine_type: str | None,
        skill_id: str,
    ) -> None:
        """Repair legacy Local Skill membership before publishing a replacement."""
        default_set = self._ensure_default_set(
            owner_id=owner_id, bot_id=bot_id, engine_type=engine_type
        )
        members = self._skill_set_repo.get_skills_in_set(str(default_set["id"]))
        if any(str(member.get("id")) == skill_id for member in members):
            return
        if not self._skill_set_repo.add_skill_to_set(
            str(default_set["id"]), skill_id, user_id=owner_id
        ):
            raise LocalSkillStorageError()

    async def _replace(
        self,
        *,
        skill: dict[str, Any],
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
        actor_id: str,
        name: str,
        description: str,
        files: list[tuple[str, bytes]],
        is_teclaw: bool,
    ) -> dict[str, Any]:
        """Publish a replacement at the stable layout-owned package locator.

        The hidden directory is staging only.  Runtime and database authority
        always converge on ``<resolved-local-root>/<skill-name>``; this keeps
        normal and Skills Pool layouts consistent and keeps implementation
        directories out of the public workspace contract.
        """
        old_locator = str(skill["git_path"])[len("local://") :]
        version_dir = f".{name}.replacement-{uuid4().hex}"
        canonical_locator, canonical = (
            self._skill_service_factory.local_skill_package_storage(
                entity_id=str(bot["entity_id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=str(bot.get("entity_type") or "staff"),
                is_desktop=bot.get("bot_type") == "desktop",
                is_teclaw=is_teclaw,
                name=name,
            )
        )
        staged_locator, staged = (
            self._skill_service_factory.local_skill_package_storage(
                entity_id=str(bot["entity_id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=str(bot.get("entity_type") or "staff"),
                is_desktop=bot.get("bot_type") == "desktop",
                is_teclaw=is_teclaw,
                name=name,
                directory_name=version_dir,
            )
        )
        old_storage = (
            self._skill_service_factory.local_skill_package_storage_for_locator(
                entity_id=str(bot["entity_id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=str(bot.get("entity_type") or "staff"),
                is_desktop=bot.get("bot_type") == "desktop",
                is_teclaw=is_teclaw,
                locator=old_locator,
            )
        )
        old_is_canonical = old_locator == canonical_locator
        obsolete_storage = old_storage
        backup = None
        old_metadata = {
            "description": skill.get("description"),
            "git_path": skill.get("git_path"),
            "user_id": skill.get("user_id"),
        }
        switched = False
        runtime_sync_attempted = False
        canonical_published = False
        try:
            await staged.write(files)
            await staged.verify()
            if old_is_canonical:
                backup_dir = f".{name}.rollback-{uuid4().hex}"
                _, backup = (
                    self._skill_service_factory.local_skill_package_storage(
                        entity_id=str(bot["entity_id"]),
                        owner_id=owner_id,
                        bot_id=bot_id,
                        engine_type=bot.get("active_engine"),
                        entity_type=str(bot.get("entity_type") or "staff"),
                        is_desktop=bot.get("bot_type") == "desktop",
                        is_teclaw=is_teclaw,
                        name=name,
                        directory_name=backup_dir,
                    )
                )
                await old_storage.copy_to(backup)
                obsolete_storage = backup
            # ``copy_to(..., replace=True)`` can fail after clearing or partly
            # writing the canonical directory.  Mark the mutation before the
            # call so every such failure restores the old authority.
            canonical_published = True
            await staged.copy_to(canonical, replace=True)
            replaced = self._skill_repo.replace_bot_local_skill(
                skill_id=str(skill["id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                old_locator=old_locator,
                new_locator=canonical_locator,
                description=description,
            )
            if replaced is None:
                raise RuntimeError("Local Skill metadata switch failed")
            switched = True
            runtime_sync_attempted = True
            if bool(skill["active"]):
                self._ensure_default_set_membership(
                    owner_id=owner_id,
                    bot_id=bot_id,
                    engine_type=bot.get("active_engine"),
                    skill_id=str(skill["id"]),
                )
            else:
                # A prior default-set exclusion can be stale after defaults
                # are recreated.  Mirror the desired inactive state into the
                # current default set before publishing the replacement.
                default_set = self._ensure_default_set(
                    owner_id=owner_id,
                    bot_id=bot_id,
                    engine_type=bot.get("active_engine"),
                )
                if not self._skill_set_repo.add_default_skill_exclusion(
                    owner_id,
                    bot_id,
                    int(default_set["id"]),
                    int(skill["id"]),
                ):
                    raise LocalSkillStorageError()
            if not self._sync_runtime(bot, owner_id, bot_id):
                raise LocalSkillRuntimeSyncError()
            self._audit_log_repo.insert(
                {
                    "bot_id": bot_id,
                    "owner_id": owner_id,
                    "operator_id": actor_id,
                    "detail": json.dumps(
                        {"action": "local_skill_replace", "skill_id": skill["id"]}
                    ),
                }
            )
        except LocalSkillRuntimeSyncError:
            await self._restore_replacement(
                skill=skill,
                old_metadata=old_metadata,
                bot=bot,
                owner_id=owner_id,
                bot_id=bot_id,
                staged=staged,
                staged_locator=staged_locator,
                canonical=canonical,
                canonical_locator=canonical_locator,
                old_is_canonical=old_is_canonical,
                backup=backup,
                canonical_published=canonical_published,
                switched=switched,
                runtime_sync_attempted=runtime_sync_attempted,
            )
            raise
        except Exception as exc:
            if switched or canonical_published:
                await self._restore_replacement(
                    skill=skill,
                    old_metadata=old_metadata,
                    bot=bot,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    staged=staged,
                    staged_locator=staged_locator,
                    canonical=canonical,
                    canonical_locator=canonical_locator,
                    old_is_canonical=old_is_canonical,
                    backup=backup,
                    canonical_published=canonical_published,
                    switched=switched,
                    runtime_sync_attempted=runtime_sync_attempted,
                )
            else:
                if backup is not None:
                    await self._discard(backup)
                await self._discard(staged)
            raise LocalSkillStorageError() from exc
        await self._discard(obsolete_storage)
        await self._discard(staged)
        return {
            "operation": "updated",
            "skill": {
                **skill,
                "description": description,
                "git_path": f"local://{canonical_locator}",
                "user_id": owner_id,
            },
            "actor_id": actor_id,
        }

    async def _restore_replacement(
        self,
        *,
        skill: dict[str, Any],
        old_metadata: dict[str, Any],
        bot: dict[str, Any],
        owner_id: str,
        bot_id: str,
        staged,
        staged_locator: str,
        canonical,
        canonical_locator: str,
        old_is_canonical: bool,
        backup,
        canonical_published: bool,
        switched: bool,
        runtime_sync_attempted: bool,
    ) -> None:
        if canonical_published:
            try:
                if old_is_canonical:
                    if backup is None:
                        raise LocalSkillStorageError()
                    await backup.copy_to(canonical, replace=True)
                elif not await canonical.cleanup():
                    raise LocalSkillStorageError()
            except Exception as exc:
                raise LocalSkillStorageError() from exc
        if switched:
            restored = self._skill_repo.update(skill["id"], old_metadata)
            if restored is None:
                raise LocalSkillStorageError()
            if runtime_sync_attempted and not self._sync_runtime(bot, owner_id, bot_id):
                raise LocalSkillRuntimeSyncError()
        if backup is not None:
            await self._discard(backup)
        await self._discard(staged)

    async def _discard(self, storage) -> None:
        try:
            cleaned = await storage.cleanup()
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        if not cleaned:
            raise LocalSkillStorageError()

    def _is_teclaw(self, *, bot_id: str, owner_id: str) -> bool:
        try:
            context = self._device_context_resolver_provider().resolve_for_bot(
                bot_id, owner_id
            )
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        return context.provider == "teclaw"
    def _same_name_matches(
        self, *, bot_id: str, owner_id: str, name: str
    ) -> list[dict[str, Any]]:
        rows = self._skill_repo.list_bot_local_by_name(bot_id=bot_id, name=name)
        owned = [row for row in rows if str(row.get("user_id")) == owner_id]
        unowned = [row for row in rows if not row.get("user_id")]
        # Only an absent owner is ambiguous legacy data.  A foreign owner is
        # known and belongs to another exact Bot scope, so it is never a
        # replacement candidate and never changes this caller's zero-match.
        if unowned:
            from agentclaw.community.core.skill_center.errors import (
                LocalSkillOwnerAmbiguousError,
            )

            raise LocalSkillOwnerAmbiguousError()
        # ``list_bot_local_by_name`` intentionally returns metadata-only rows
        # for duplicate detection.  Replacement also needs the current desired
        # activation state, which is derived from the default-set exclusion and
        # is exposed by the exact single-row query.
        matches: list[dict[str, Any]] = []
        for row in owned:
            current = self._skill_repo.get_bot_local_skill(
                skill_id=str(row["id"]), bot_id=bot_id, user_id=owner_id
            )
            if current is None:
                continue
            matches.append({**row, "active": bool(current["active"])})
        return matches

    def _sync_runtime(self, bot: dict[str, Any], owner_id: str, bot_id: str) -> bool:
        try:
            service = self._skill_set_service_factory.create(
                user_id=owner_id,
                entity_id=str(bot["entity_id"]),
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=bot.get("entity_type"),
            )
            return bool(service.sync_runtime())
        except Exception:
            return False

    @staticmethod
    def _scope_for(bot: dict[str, Any], bot_id: str) -> BotSkillLayoutScope:
        return BotSkillLayoutScope(
            env=str(bot["env"]), entity_id=str(bot["entity_id"]), bot_id=bot_id
        )

    def _authorize(self, bot_id: str, owner_id: str, actor_id: str) -> dict[str, Any]:
        bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
        if bot is None:
            from agentclaw.community.core.skill_center.errors import (
                LocalSkillNotFoundError,
            )

            raise LocalSkillNotFoundError()
        if actor_id != owner_id:
            permission = self._collaborators.check_collaborator_permission(
                bot_id, owner_id, actor_id, PermissionLevel.MEMBER
            )
            if not permission.get("has_permission"):
                from agentclaw.community.core.skill_center.errors import (
                    LocalSkillNotFoundError,
                )

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
        metadata = SkillParser.parse_content(text) or {}
        if not metadata.get("name") or not metadata.get("description"):
            try:
                raw_metadata = yaml.safe_load(text)
            except yaml.YAMLError:
                raw_metadata = None
            if isinstance(raw_metadata, dict):
                metadata = raw_metadata
        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            raise LocalSkillInvalidPackageError()
        name, description = name.strip(), description.strip()
        if (
            not name
            or not description
            or not _NAME.fullmatch(name)
            or name.lower() in {"skills-center", "skills-local", "skills-repo"}
        ):
            raise LocalSkillInvalidPackageError()
        if wrapper and wrapper != name:
            raise LocalSkillInvalidPackageError()
        if wrapper is not None and any(
            not path.startswith(f"{wrapper}/") for path, _ in files
        ):
            raise LocalSkillInvalidPackageError()
        normalized = [(p[len(wrapper) + 1 :] if wrapper else p, c) for p, c in files]
        return name, description, normalized
