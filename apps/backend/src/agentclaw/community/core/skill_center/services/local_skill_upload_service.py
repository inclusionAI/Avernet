"""Create-or-replace lifecycle for a Bot-owned Local Skill.

The service owns validation and compensation; the HTTP adapter only translates
the raw request body and maps the public response.
"""

from __future__ import annotations

from contextlib import nullcontext

from agentclaw.community.core.skill_center.manifest_defaults import (
    PackageWriteGuard,
    ManifestPackageWriteCommittedError,
)

import asyncio
import hashlib
import uuid
from threading import Event
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, Callable, Optional
from uuid import uuid4

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.addressing import (
    BotAddressResolver,
    stable_resource_owner_id,
)
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.repository.protocols.bot import BotRepository
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
    LocalSkillStorageError,
    SkillAssetInUseError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.skill_center.factories import (
    SkillServiceFactory,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skill_center.services.local_skill_package_runtime import (
    LocalSkillPackageRuntime,
    LocalSkillPackageRuntimeResult,
)
from agentclaw.community.core.skill_center.local_skill_security_upload_hook import (
    LocalSkillSecurityUploadHook,
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
from agentclaw.community.log import get_logger
from injector import inject
from agentclaw.community.core.skill_center.local_skill_upload_service_protocol import (
    LocalSkillUploadServiceProtocol,
)

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )

logger = get_logger()
SECURITY_HANDOFF_TIMEOUT_SECONDS = 2.0
SECURITY_FAILURE_RECORD_TIMEOUT_SECONDS = 0.25


class LocalSkillUploadService(LocalSkillUploadServiceProtocol):
    """Authorize, validate, persist and associate an inactive Local Skill."""

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        collaborator_service: CollaboratorServiceProtocol,
        skill_service_factory: SkillServiceFactory,
        edit_guard: SkillsPoolEditGuard,
        device_context_resolver_provider: Callable[[], "DeviceContextResolver"],
        runtime_reconciler: BotRuntimeProjectorProtocol,
        package_validator: SkillPackageValidator,
        package_runtime: LocalSkillPackageRuntime,
        security_observer_provider: Callable[[], LocalSkillSecurityUploadHook],
        security_observation_enabled: bool,
    ) -> None:
        self._skill_repo = skill_repo
        self._bot_repo = bot_repo
        self._bot_addresses = BotAddressResolver(bot_repo)
        self._collaborators = collaborator_service
        self._skill_service_factory = skill_service_factory
        self._edit_guard = edit_guard
        self._device_context_resolver_provider = device_context_resolver_provider
        self._runtime_reconciler = runtime_reconciler
        self._package_validator = package_validator
        self._package_runtime = package_runtime
        self._security_observer_provider = security_observer_provider
        self._security_observation_enabled = security_observation_enabled

    async def installed_package_digest(
        self,
        *,
        bot: Mapping[str, Any],
        bot_id: str,
        owner_id: str,
        name: str,
    ) -> Optional[str]:
        """The installed package's canonical digest, or ``None`` if absent.

        Reads the bytes actually published under the skill's stable
        layout-owned locator — the same storage read ``verify``/``copy_to``
        perform, no locator knowledge of the caller's own — and answers the
        sha256 of their canonical repack, so a caller holding the same
        package can ask "is what is installed *this* content?".

        Unreadable is *unknown* (``None``), not *equal*: storage that cannot
        be read back must not license an unchanged verdict — the caller
        classes the entry for a full write and the write path restores the
        package while it is in hand.
        """
        import hashlib

        try:
            _, storage = self._skill_service_factory.local_skill_package_storage(
                entity_id=str(bot["entity_id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=str(bot.get("entity_type") or "staff"),
                is_desktop=bot.get("bot_type") == "desktop",
                is_teclaw=self._is_teclaw(bot_id=bot_id, owner_id=owner_id),
                name=name,
            )
            if not await storage.exists():
                return None
            files = await storage.read_package_files()
        except (LocalSkillStorageError, OSError):
            # A locator that cannot be reached, or a package that cannot be
            # walked — ``_is_teclaw`` resolves the device context, so its
            # failure is the same class here: unknown, per the docstring —
            # never a crash of the caller's apply, and never a silent
            # "unchanged" either.
            return None
        try:
            canonical = self._package_validator.pack_directory(list(files))
        except (SkillPackageInvalidError, SkillPackageTooLargeError):
            # Bytes on disk that no longer form a valid package: the installed
            # state has drifted from anything this service would publish.
            # Unknown, not equal — the write path replaces it.
            return None
        return "sha256:" + hashlib.sha256(canonical).hexdigest()

    async def export_installed_package(
        self, *, bot: Mapping[str, Any], bot_id: str, owner_id: str, name: str
    ) -> bytes:
        _, storage = self._skill_service_factory.local_skill_package_storage(
            entity_id=str(bot["entity_id"]),
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=bot["active_engine"],
            entity_type=str(bot["entity_type"]),
            is_desktop=bot.get("bot_type") == "desktop",
            is_teclaw=self._is_teclaw(bot_id=bot_id, owner_id=owner_id),
            name=name,
        )
        if not await storage.exists():
            raise LocalSkillStorageError("Installed Skill package is missing")
        files = await storage.read_package_files()
        package = self._package_validator.pack_directory(list(files))
        self._package_validator.validate_legacy_local_zip(package)
        return package

    async def upload_local_skill(
        self,
        *,
        bot_id: str,
        owner_id: str,
        actor_id: str,
        package: bytes,
        write_guard: PackageWriteGuard | None = None,
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
        package_published = False
        try:
            bot = self._authorize(bot_id, owner_id, actor_id)
            if self._scope_for(bot, bot_id) != scope:
                from agentclaw.community.core.skill_center.errors import (
                    LocalSkillNotFoundError,
                )

                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            owner_id = stable_resource_owner_id(bot)
            validated = self._validate_zip(package)
            is_teclaw = self._is_teclaw(bot_id=bot_id, owner_id=owner_id)
            # Re-read same-name candidates, owner, readiness and default state
            # under the edit lock.  Uploader identity is intentionally absent.
            matches = self._same_name_matches(
                bot_id=bot_id, owner_id=owner_id, name=validated.name
            )
            if len(matches) > 1:
                raise LocalSkillDuplicateError()
            observation_id = str(uuid.uuid4())
            digest = "sha256:" + hashlib.sha256(validated.canonical_zip).hexdigest()
            observe = self._security_observation_enabled
            location = self._skill_service_factory.local_skill_package_location(
                entity_id=str(bot["entity_id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                engine_type=bot.get("active_engine"),
                entity_type=str(bot.get("entity_type") or "staff"),
                is_desktop=bot.get("bot_type") == "desktop",
                is_teclaw=is_teclaw,
                name=validated.name,
            )
            with write_guard() if write_guard is not None else nullcontext():
                runtime_result = await self._package_runtime.apply(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    skill_name=validated.name,
                    layout=location.layout,
                    package=validated.canonical_zip,
                )
            if runtime_result is not None:
                package_published = True
                if matches:
                    result = await self._commit_runtime_replace(
                        skill=matches[0],
                        owner_id=owner_id,
                        bot_id=bot_id,
                        actor_id=actor_id,
                        description=validated.description,
                        runtime_result=runtime_result,
                    )
                else:
                    result = self._commit_runtime_create(
                        owner_id=owner_id,
                        bot_id=bot_id,
                        actor_id=actor_id,
                        name=validated.name,
                        description=validated.description,
                        directory=location.directory,
                        runtime_result=runtime_result,
                    )
            elif matches:
                result = await self._replace(
                    write_guard=write_guard,
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
            else:
                result = await self._create(
                    write_guard=write_guard,
                    bot=bot,
                    owner_id=owner_id,
                    bot_id=bot_id,
                    actor_id=actor_id,
                    name=validated.name,
                    description=validated.description,
                    files=list(validated.files),
                    is_teclaw=is_teclaw,
                )
            request_cancelled = False
            handoff_cancelled = Event()
            if not observe:
                return result
            try:
                observer = self._security_observer_provider()
                # Formal persistence has succeeded and the Bot edit lock is
                # still held. A failed identity bind skips observation only.
                incarnation_id = observer.current_incarnation_id(
                    bot_id=bot_id,
                    owner_id=owner_id,
                    skill_id=int(result["skill"]["id"]),
                )
                if incarnation_id is None:
                    raise ValueError("formal Local Skill security UUID is unavailable")
                handoff = asyncio.create_task(
                    asyncio.to_thread(
                        observer.observe,
                        bot_id=bot_id,
                        owner_id=owner_id,
                        actor_id=actor_id,
                        skill_id=int(result["skill"]["id"]),
                        operation=result["operation"],
                        name=validated.name,
                        description=validated.description,
                        canonical_zip=validated.canonical_zip,
                        observation_id=observation_id,
                        incarnation_id=incarnation_id,
                        cancelled=handoff_cancelled,
                    )
                )
                deadline = (
                    asyncio.get_running_loop().time() + SECURITY_HANDOFF_TIMEOUT_SECONDS
                )
                while (
                    not handoff.done() and asyncio.get_running_loop().time() < deadline
                ):
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(handoff),
                            timeout=max(
                                0.0, deadline - asyncio.get_running_loop().time()
                            ),
                        )
                    except asyncio.TimeoutError:
                        break
                    except asyncio.CancelledError:
                        request_cancelled = True
                if handoff.done():
                    handoff.result()
                else:
                    handoff_cancelled.set()
                    logger.error(
                        "Local Skill security handoff failed stage=TIMEOUT "
                        "bot_id=%s owner_id=%s actor_id=%s skill_id=%s "
                        "observation_id=%s operation=%s digest=%s",
                        bot_id,
                        owner_id,
                        actor_id,
                        result["skill"]["id"],
                        observation_id,
                        result["operation"],
                        digest,
                    )
                    failure_record = asyncio.create_task(
                        asyncio.to_thread(
                            observer.record_handoff_timeout,
                            bot_id=bot_id,
                            owner_id=owner_id,
                            actor_id=actor_id,
                            skill_id=int(result["skill"]["id"]),
                            observation_id=observation_id,
                            digest=digest,
                            incarnation_id=incarnation_id,
                            name=validated.name,
                            description=validated.description,
                            creator_id=actor_id
                            if result["operation"] == "created"
                            else None,
                        )
                    )
                    failure_record.add_done_callback(self._log_late_security_handoff)
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(failure_record),
                            timeout=SECURITY_FAILURE_RECORD_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        pass
                    except asyncio.CancelledError:
                        request_cancelled = True
                    # Completion is deliberately detached. A late OSS result
                    # cannot publish a current observation once cancelled.
                    handoff.add_done_callback(self._log_late_security_handoff)
            except Exception:
                logger.exception(
                    "[LocalSkillUploadService] security observation handoff failed "
                    "bot_id=%s owner_id=%s actor_id=%s skill_id=%s "
                    "observation_id=%s operation=%s digest=%s",
                    bot_id,
                    owner_id,
                    actor_id,
                    result["skill"]["id"],
                    observation_id,
                    result["operation"],
                    digest,
                )
            if request_cancelled:
                raise asyncio.CancelledError
            return result
        except Exception as exc:
            if write_guard is not None and package_published:
                raise ManifestPackageWriteCommittedError(
                    "Package published before upload failure"
                ) from exc
            raise
        finally:
            self._edit_guard.release(lease)

    @staticmethod
    def _log_late_security_handoff(task: asyncio.Task[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Local Skill late security handoff failed")

    def _commit_runtime_create(
        self,
        *,
        owner_id: str,
        bot_id: str,
        actor_id: str,
        name: str,
        description: str,
        directory: str,
        runtime_result: LocalSkillPackageRuntimeResult,
    ) -> dict[str, Any]:
        """Persist metadata after Engine success without compensating file I/O."""

        try:
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
        except Exception as exc:
            logger.error(
                "[LocalSkillUploadService] post-apply persistence failed "
                "bot_id=%s skill_name=%s action=%s digest=%s target_path=%s",
                bot_id,
                name,
                runtime_result.action,
                runtime_result.content_digest,
                runtime_result.target_path,
            )
            raise LocalSkillStorageError() from exc
        return {
            "operation": "created",
            "skill": {**skill, "active": False},
            "actor_id": actor_id,
        }

    async def _commit_runtime_replace(
        self,
        *,
        skill: dict[str, Any],
        owner_id: str,
        bot_id: str,
        actor_id: str,
        description: str,
        runtime_result: LocalSkillPackageRuntimeResult,
    ) -> dict[str, Any]:
        """Persist replace metadata while preserving the historical locator."""

        old_locator = str(skill["git_path"])[len("local://") :]
        try:
            replaced = self._skill_repo.replace_bot_local_skill(
                skill_id=str(skill["id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                old_locator=old_locator,
                new_locator=old_locator,
                description=description,
            )
            if replaced is None:
                raise RuntimeError("Local Skill metadata switch failed")
            runtime_projection = await self._sync_runtime(owner_id, bot_id)
        except Exception as exc:
            logger.error(
                "[LocalSkillUploadService] post-apply persistence failed "
                "bot_id=%s skill_name=%s action=%s digest=%s target_path=%s",
                bot_id,
                skill["name"],
                runtime_result.action,
                runtime_result.content_digest,
                runtime_result.target_path,
            )
            raise LocalSkillStorageError() from exc
        return {
            "operation": "updated",
            "skill": {
                **skill,
                "description": description,
                "git_path": skill["git_path"],
                "user_id": owner_id,
            },
            "actor_id": actor_id,
            "runtime_projection": runtime_projection.to_dict(),
        }

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
        write_guard: PackageWriteGuard | None = None,
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
        physical_started = False
        physical_published = False
        try:
            # A previous failed first upload has no authoritative record, but
            # must not be mixed into this package on a retry.
            with write_guard() if write_guard is not None else nullcontext():
                physical_started = True
                await storage.prepare()
                await storage.write(files)
                physical_published = True
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
            compensation_failed = False
            if skill is not None:
                try:
                    self._skill_repo.delete(skill["id"])
                except SkillAssetInUseError as conflict:
                    raise conflict from exc
                except Exception:
                    compensation_failed = True
            try:
                if physical_started:
                    with write_guard() if write_guard is not None else nullcontext():
                        await storage.cleanup()
            except Exception:
                compensation_failed = True
            if write_guard is not None and physical_published and compensation_failed:
                raise ManifestPackageWriteCommittedError("Published package cleanup was blocked or failed") from exc
            raise LocalSkillStorageError() from exc

    async def _replace(
        self,
        *,
        write_guard: PackageWriteGuard | None = None,
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
        _, canonical = self._skill_service_factory.local_skill_package_storage(
            entity_id=str(bot["entity_id"]),
            owner_id=owner_id,
            bot_id=bot_id,
            engine_type=bot.get("active_engine"),
            entity_type=str(bot.get("entity_type") or "staff"),
            is_desktop=bot.get("bot_type") == "desktop",
            is_teclaw=is_teclaw,
            name=name,
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
        try:
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
                    skill_name=name,
                )
            )
        except Exception as exc:
            raise LocalSkillStorageError() from exc
        has_old_package = await old_storage.exists()
        # Factory resolution proves that the recorded locator identifies this
        # stable layout-owned package.  It may be a supported historical alias,
        # but missing bytes still cannot be manufactured from metadata.
        if not has_old_package:
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
            with write_guard() if write_guard is not None else nullcontext():
                canonical_published = True
                await staged.copy_to(canonical, replace=True)
            replaced = self._skill_repo.replace_bot_local_skill(
                skill_id=str(skill["id"]),
                owner_id=owner_id,
                bot_id=bot_id,
                old_locator=old_locator,
                new_locator=old_locator,
                description=description,
            )
            if replaced is None:
                raise RuntimeError("Local Skill metadata switch failed")
            switched = True
            runtime_sync_attempted = True
            runtime_projection = await self._sync_runtime(owner_id, bot_id)
        except Exception as exc:
            if switched or canonical_published:
                await self._restore_replacement(
                    write_guard=write_guard,
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
                    write_guard=write_guard,
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
            except ManifestPackageWriteCommittedError:
                raise
            except Exception as rollback_exc:
                raise LocalSkillStorageError() from rollback_exc
            raise LocalSkillStorageError() from exc
        return {
            "operation": "updated",
            "skill": {
                **skill,
                "description": description,
                "git_path": skill["git_path"],
                "user_id": owner_id,
            },
            "actor_id": actor_id,
            "runtime_projection": runtime_projection.to_dict(),
        }

    async def _restore_replacement(
        self,
        *,
        write_guard: PackageWriteGuard | None = None,
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
        try:
            if canonical_published:
                try:
                    if backup is None:
                        raise LocalSkillStorageError()
                    with write_guard() if write_guard is not None else nullcontext():
                        await backup.copy_to(canonical, replace=True)
                except Exception as exc:
                    raise LocalSkillStorageError() from exc
            if switched:
                restored = self._skill_repo.update(skill["id"], old_metadata)
                if restored is None:
                    raise LocalSkillStorageError()
                if runtime_sync_attempted:
                    await self._sync_runtime(owner_id, bot_id)
            if backup is not None:
                await self._discard(backup)
            await self._discard(staged)

        except Exception as exc:
            if write_guard is not None and canonical_published:
                raise ManifestPackageWriteCommittedError(
                    "Published package restore was blocked or failed"
                ) from exc
            raise

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

    async def _sync_runtime(
        self, owner_id: str, bot_id: str
    ) -> RuntimeProjectionResult:
        try:
            # Skills only. Both callers are the Local Skill *replace* flow
            # (and its compensating restore), and a replace cannot move the
            # MCP set: ``replace_bot_local_skill`` writes ``description``,
            # ``user_id`` and ``gmt_modified``, refuses outright to change
            # ``git_path``, and never touches ``mcp_dependencies`` — nothing
            # here rescans them either. The projected MCP codes are therefore
            # identical before and after, so claiming or releasing anything
            # would be a device write to restate what is already true.
            result = await self._runtime_reconciler.project(
                bot_id=bot_id,
                owner_id=owner_id,
                scope=ProjectionScope(skills=True),
            )
            return result or RuntimeProjectionResult.converged()
        except Exception:
            return RuntimeProjectionResult.pending(
                code="SKILL_RUNTIME_UNAVAILABLE",
                reason="Skill 运行环境当前不可连接，内容已更新但尚未同步",
            )

    @staticmethod
    def _scope_for(bot: dict[str, Any], bot_id: str) -> BotSkillLayoutScope:
        return BotSkillLayoutScope(
            env=str(bot["env"]), entity_id=str(bot["entity_id"]), bot_id=bot_id
        )

    def _authorize(self, bot_id: str, owner_id: str, actor_id: str) -> dict[str, Any]:
        try:
            bot = self._bot_addresses.resolve(
                bot_id=bot_id, legacy_owner_id=owner_id
            ).bot
        except BotAddressNotFoundError:
            from agentclaw.community.core.skill_center.errors import (
                LocalSkillNotFoundError,
            )

            raise LocalSkillNotFoundError()
        current_owner = str(bot["owner_id"])
        if actor_id != current_owner:
            # Uploading installs executable content on someone's Bot: the
            # edit/operations domain, where the Space-synthesized MEMBER has
            # no say (its openapi twin rows carry Check … explicit=True; a
            # space member could upload but not delete — the split-between
            # -writes wart the review caught).
            permission = self._collaborators.check_collaborator_permission(
                bot_id, current_owner, actor_id, PermissionLevel.MEMBER, explicit=True
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
