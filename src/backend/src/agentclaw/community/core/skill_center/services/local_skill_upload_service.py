"""Create-or-replace lifecycle for a Bot-owned Local Skill.

The service owns validation and compensation; the HTTP adapter only translates
the raw request body and maps the public response.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Callable
from uuid import uuid4

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import (
    BotCollabLogRepositoryProtocol,
    BotRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillRepository,
)
from agentclaw.community.core.skill_center.errors import (
    LocalSkillDuplicateError,
    LocalSkillEditBusyError,
    LocalSkillEditLockUnavailableError,
    LocalSkillEditPausedError,
    LocalSkillInvalidPackageError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotReadyError,
    LocalSkillRuntimeSyncError,
    LocalSkillStorageError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.skill_center.factories import (
    SkillServiceFactory,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
)
from agentclaw.community.core.skill_center.skill_package import (
    SkillPackageInvalidError,
    SkillPackageTooLargeError,
    SkillPackageValidator,
    ValidatedSkillPackage,
)
from agentclaw.community.core.skills_pool.edit_guard import (
    SkillsPoolEditBusyError,
    SkillsPoolEditGuard,
    SkillsPoolEditLockUnavailableError,
    SkillsPoolEditPausedError,
    SkillsPoolEditRollbackError,
)
from agentclaw.community.core.skills_pool.types import BotSkillLayoutScope
from injector import inject
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import LocalSkillUploadServiceProtocol

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )


class LocalSkillUploadService(LocalSkillUploadServiceProtocol):
    """Authorize, validate, persist and associate an inactive Local Skill."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_service_factory: SkillServiceFactory,
        audit_log_repo: BotCollabLogRepositoryProtocol,
        edit_guard: SkillsPoolEditGuard,
        device_context_resolver_provider: Callable[[], "DeviceContextResolver"],
        runtime_reconciler: BotRuntimeProjectorProtocol,
        package_validator: SkillPackageValidator,
    ) -> None:
        self._skill_repo = skill_repo
        self._bot_repo = bot_repo
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory
        self._audit_log_repo = audit_log_repo
        self._edit_guard = edit_guard
        self._device_context_resolver_provider = device_context_resolver_provider
        self._runtime_reconciler = runtime_reconciler
        self._package_validator = package_validator

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
            validated = self._validate_zip(package)
            is_teclaw = self._is_teclaw(bot_id=bot_id, owner_id=owner_id)
            # Re-read same-name candidates, owner, readiness and default state
            # under the edit lock.  Uploader identity is intentionally absent.
            matches = self._same_name_matches(
                bot_id=bot_id, owner_id=owner_id, name=validated.name
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
                    name=validated.name,
                    description=validated.description,
                    files=list(validated.files),
                    is_teclaw=is_teclaw,
                )
            return await self._create(
                bot=bot,
                owner_id=owner_id,
                bot_id=bot_id,
                actor_id=actor_id,
                name=validated.name,
                description=validated.description,
                files=list(validated.files),
                is_teclaw=is_teclaw,
            )
        finally:
            self._edit_guard.release(lease)

    async def upload_local_skill_files(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        files: Sequence[tuple[str, bytes]],
    ) -> dict[str, Any]:
        """Accept a browser directory selection through the ZIP authority.

        The old product API represents a directory as a flat multipart file
        list plus relative paths.  Repackage it once here, then reuse the
        complete ZIP validation, same-name replacement and compensation flow.
        """
        if len(files) == 1 and files[0][0].endswith(".zip"):
            package = files[0][1]
        else:
            package = self._pack_directory(files)
        return await self.upload_local_skill(
            bot_id=bot_id,
            owner_id=owner_id,
            actor_id=actor_id,
            package=package,
        )

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
        _, staged = self._skill_service_factory.local_skill_package_storage(
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
        has_old_package = await old_storage.exists()
        # Replacement is defined only for the stable layout-owned locator.
        # It must never migrate ``git_path`` or manufacture a new package for a
        # metadata row whose old bytes are already missing: either case would
        # leave no authoritative old package to restore on failure.
        if old_locator != canonical_locator or not has_old_package:
            raise LocalSkillStorageError()
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
            backup_dir = f".{name}.rollback-{uuid4().hex}"
            _, backup = self._skill_service_factory.local_skill_package_storage(
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
            await old_storage.copy_to(backup)
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
            if not await self._sync_runtime(owner_id, bot_id):
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
                owner_id=owner_id,
                bot_id=bot_id,
                staged=staged,
                canonical=canonical,
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
                    owner_id=owner_id,
                    bot_id=bot_id,
                    staged=staged,
                    canonical=canonical,
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
        try:
            # Keep the rollback copy until every other temporary package has
            # been removed.  If any cleanup fails, the operation still has the
            # bytes required to restore the old canonical package.
            await self._discard(staged)
            await self._discard(backup)
        except Exception as exc:
            try:
                await self._restore_replacement(
                    skill=skill,
                    old_metadata=old_metadata,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    staged=staged,
                    canonical=canonical,
                    backup=backup,
                    canonical_published=canonical_published,
                    switched=switched,
                    runtime_sync_attempted=runtime_sync_attempted,
                )
            except Exception as rollback_exc:
                raise LocalSkillStorageError() from rollback_exc
            raise LocalSkillStorageError() from exc
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
        owner_id: str,
        bot_id: str,
        staged,
        canonical,
        backup,
        canonical_published: bool,
        switched: bool,
        runtime_sync_attempted: bool,
    ) -> None:
        if canonical_published:
            try:
                if backup is None:
                    raise LocalSkillStorageError()
                await backup.copy_to(canonical, replace=True)
            except Exception as exc:
                raise LocalSkillStorageError() from exc
        if switched:
            restored = self._skill_repo.update(skill["id"], old_metadata)
            if restored is None:
                raise LocalSkillStorageError()
            if runtime_sync_attempted and not await self._sync_runtime(
                owner_id, bot_id
            ):
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

    async def _sync_runtime(self, owner_id: str, bot_id: str) -> bool:
        try:
            # Skills only. Both callers are the Local Skill *replace* flow
            # (and its compensating restore), and a replace cannot move the
            # MCP set: ``replace_bot_local_skill`` writes ``description``,
            # ``user_id`` and ``gmt_modified``, refuses outright to change
            # ``git_path``, and never touches ``mcp_dependencies`` — nothing
            # here rescans them either. The projected MCP codes are therefore
            # identical before and after, so claiming or releasing anything
            # would be a device write to restate what is already true.
            await self._runtime_reconciler.project(
                bot_id=bot_id,
                owner_id=owner_id,
                scope=ProjectionScope(skills=True),
            )
            return True
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

    def _validate_zip(self, package: bytes) -> ValidatedSkillPackage:
        try:
            return self._package_validator.validate_legacy_local_zip(package)
        except SkillPackageInvalidError as exc:
            raise LocalSkillInvalidPackageError(exc.reason) from exc
        except SkillPackageTooLargeError as exc:
            raise LocalSkillTooLargeError() from exc

    def _pack_directory(self, files: Sequence[tuple[str, bytes]]) -> bytes:
        try:
            return self._package_validator.pack_directory(files)
        except SkillPackageInvalidError as exc:
            raise LocalSkillInvalidPackageError(exc.reason) from exc
        except SkillPackageTooLargeError as exc:
            raise LocalSkillTooLargeError() from exc
