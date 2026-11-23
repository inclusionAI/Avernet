"""Create-or-replace lifecycle for a Bot-owned Local Skill.

The service owns validation and compensation; the HTTP adapter only translates
the raw request body and maps the public response.
"""

from __future__ import annotations

import asyncio
import hashlib
from time import perf_counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Callable, Optional
from uuid import uuid4

from agentclaw.community.core.bot_collaborator.models import PermissionLevel
from agentclaw.community.core.bot_collaborator.protocols import (
    CollaboratorServiceProtocol,
)
from agentclaw.community.core.bot_management.addressing import BotAddressResolver, stable_resource_owner_id
from agentclaw.community.core.bot_management.errors import BotAddressNotFoundError
from agentclaw.community.core.bot_management.readiness import is_bot_ready
from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    RuntimeBindingChangedError,
    RuntimeBindingTarget,
    expect_runtime_binding,
)
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
    LocalSkillNotFoundError,
    LocalSkillPackageDispatchPendingError,
    LocalSkillPackageOutcomeUnknownError,
    LocalSkillRuntimeUnavailableError,
    LocalSkillLayoutRollbackError,
    LocalSkillNotReadyError,
    LocalSkillStorageError,
    SkillAssetInUseError,
    LocalSkillTooLargeError,
)
from agentclaw.community.core.skill_center.factories import (
    LocalSkillPackageLocation,
    SkillServiceFactory,
)
from agentclaw.community.core.skill_center.runtime_projection_contract import (
    BotRuntimeProjectorProtocol,
    ProjectionScope,
    RuntimeProjectionResult,
)
from agentclaw.community.core.skill_center.services.local_skill_upload_dst_asset import (
    LocalSkillUploadDstAssetMixin,
)
from agentclaw.community.core.skill_center.services.local_skill_package_runtime import (
    LocalSkillPackageDestination,
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
    SkillsPoolEditLease,
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

from agentclaw.community.core.ports.skill_package_upload_port import (
    LocalSkillPackageReceipt,
    LocalSkillPackageWriteResult,
    PackageMetadata,
    PackagePublication,
)
from agentclaw.community.core.skill_center.services.local_skill_security_handoff import (
    handoff_local_skill_security,
)
from agentclaw.community.core.skill_center.services.local_skill_batch import (
    PreparedLocalSkill,
    dispatch_local_skill_batch,
    package_result,
    prepare_local_skill_packages,
)

if TYPE_CHECKING:
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )

logger = get_logger()
SECURITY_HANDOFF_TIMEOUT_SECONDS = 2.0
SECURITY_FAILURE_RECORD_TIMEOUT_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class _LocalSkillEdit:
    bot_id: str
    owner_id: str
    actor_id: str
    bot: dict[str, Any]
    lease: SkillsPoolEditLease


@dataclass(frozen=True, slots=True)
class _LocalSkillTarget:
    skill: PreparedLocalSkill
    location: LocalSkillPackageLocation
    existing: dict[str, Any] | None


class LocalSkillUploadService(
    LocalSkillUploadDstAssetMixin, LocalSkillUploadServiceProtocol
):
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
        local_skill_parallelism: int = 1,
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
        if type(local_skill_parallelism) is not int or not 1 <= local_skill_parallelism <= 4:
            raise ValueError("Local Skill batch parallelism must be an integer from 1 to 4")
        self._local_skill_parallelism = local_skill_parallelism

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
        self, *, bot_id: str, owner_id: str, actor_id: str, package: bytes
    ) -> dict[str, Any]:
        async with self._edit(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id) as edit:
            skill = PreparedLocalSkill.from_package(self._validate_zip(package))
            context = self._device_context(bot_id=bot_id, owner_id=edit.owner_id)
            if self._skill_repo.has_pending_local_skill_dispatch(
                bot_id=bot_id, entity_id=edit.owner_id, name=skill.package.name,
                device_id=context.device_id,
            ):
                raise LocalSkillPackageDispatchPendingError()
            is_teclaw = context.provider == "teclaw"
            binding = RuntimeBindingTarget(
                bot_id, str(edit.bot["owner_id"]), edit.owner_id, context.binding_id, context.device_id,
            )
            try:
                with expect_runtime_binding(binding):
                    target = self._target(edit, skill, is_teclaw=is_teclaw)
                    runtime_result = await self._package_runtime.apply(
                        bot_id=bot_id, owner_id=edit.owner_id, skill_name=skill.package.name,
                        layout=target.location.layout, package=skill.package.canonical_zip,
                    )
                    result = await self._persist_package(
                        edit, target, runtime_result, is_teclaw=is_teclaw, deferred=False
                    )
            except RuntimeBindingChangedError as exc:
                raise LocalSkillRuntimeUnavailableError() from exc
            await self._observe_package(edit, skill, result)
        # Capability delivery and Apply use a common send boundary. Enter it
        # after releasing the package lease to keep their lock order acyclic.
        if target.existing is not None:
            projection = await self._sync_runtime(edit.owner_id, bot_id)
            result["runtime_projection"] = projection.to_dict()
        return result

    @asynccontextmanager
    async def _edit(
        self, *, bot_id: str, owner_id: str, actor_id: str,
    ) -> AsyncIterator[_LocalSkillEdit]:
        # The pre-lock read exists only to address the distributed lock.  Every
        # authoritative decision is deliberately repeated after acquisition.
        initial_bot = self._authorize(bot_id, owner_id, actor_id)
        scope = self._scope_for(initial_bot, bot_id)
        lock_started = perf_counter()
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
        logger.info(
            "Local Skill edit acquired bot_id=%s lock_wait_ms=%.3f",
            bot_id, (perf_counter() - lock_started) * 1000,
        )
        try:
            bot = self._authorize(bot_id, owner_id, actor_id)
            if self._scope_for(bot, bot_id) != scope:
                raise LocalSkillNotFoundError()
            if not is_bot_ready(bot):
                raise LocalSkillNotReadyError()
            yield _LocalSkillEdit(bot_id, stable_resource_owner_id(bot), actor_id, bot, lease)
        finally:
            self._edit_guard.release(lease)

    def _target(
        self, edit: _LocalSkillEdit, skill: PreparedLocalSkill, *, is_teclaw: bool,
    ) -> _LocalSkillTarget:
        bot = edit.bot
        name = skill.package.name
        matches = self._same_name_matches(bot_id=edit.bot_id, owner_id=edit.owner_id, name=name)
        if len(matches) > 1:
            raise LocalSkillDuplicateError()
        location = self._skill_service_factory.local_skill_package_location(
            entity_id=str(bot["entity_id"]),
            owner_id=edit.owner_id,
            bot_id=edit.bot_id,
            engine_type=bot.get("active_engine"),
            entity_type=str(bot.get("entity_type") or "staff"),
            is_desktop=bot.get("bot_type") == "desktop",
            is_teclaw=is_teclaw,
            name=name,
        )
        return _LocalSkillTarget(skill, location, matches[0] if matches else None)

    async def _persist_package(
        self, edit: _LocalSkillEdit, target: _LocalSkillTarget,
        runtime_result: LocalSkillPackageRuntimeResult | None, *,
        is_teclaw: bool, deferred: bool,
    ) -> dict[str, Any]:
        validated = target.skill.package
        who = dict(bot_id=edit.bot_id, owner_id=edit.owner_id, actor_id=edit.actor_id)
        if runtime_result is not None:
            if target.existing is not None:
                return self._commit_runtime_replace(
                    **who, skill=target.existing, description=validated.description,
                    runtime_result=runtime_result, deferred=deferred, is_teclaw=is_teclaw,
                )
            return self._commit_runtime_create(
                **who, name=validated.name, description=validated.description,
                directory=target.location.directory, runtime_result=runtime_result,
            )
        if target.existing is not None:
            return await self._replace(
                **who, skill=target.existing, bot=edit.bot, name=validated.name,
                description=validated.description, files=list(validated.files),
                is_teclaw=is_teclaw, deferred=deferred,
            )
        return await self._create(
            **who, bot=edit.bot, name=validated.name, description=validated.description,
            files=list(validated.files), is_teclaw=is_teclaw,
        )

    async def _observe_package(
        self, edit: _LocalSkillEdit, skill: PreparedLocalSkill, result: dict[str, Any],
    ) -> None:
        if not self._security_observation_enabled:
            return
        await handoff_local_skill_security(
            observer_provider=self._security_observer_provider,
            bot_id=edit.bot_id, owner_id=edit.owner_id, actor_id=edit.actor_id,
            skill=skill, skill_id=int(result["skill"]["id"]), operation=result["operation"],
            handoff_timeout_seconds=SECURITY_HANDOFF_TIMEOUT_SECONDS,
            record_timeout_seconds=SECURITY_FAILURE_RECORD_TIMEOUT_SECONDS,
        )

    async def upload_local_skill_batch(
        self, *, bot_id: str, owner_id: str, actor_id: str,
        packages: Sequence[tuple[str, bytes]],
    ) -> tuple[LocalSkillPackageWriteResult, ...]:
        if not packages:
            return ()
        async with self._edit(bot_id=bot_id, owner_id=owner_id, actor_id=actor_id) as edit:
            started = perf_counter()
            prepared, unattempted = await prepare_local_skill_packages(packages, validate=self._validate_zip)
            logger.info(
                "Local Skill batch prepared bot_id=%s package_count=%d prepare_ms=%.3f",
                bot_id, len(packages), (perf_counter() - started) * 1000,
            )
            if len(prepared) != len(packages):
                return unattempted
            destination = await self._package_runtime.destination(
                bot_id=bot_id, owner_id=edit.owner_id
            )
            targets: list[_LocalSkillTarget] = []
            directories: set[str] = set()
            for index, skill in enumerate(prepared):
                try:
                    target = self._target(edit, skill, is_teclaw=destination.is_teclaw)
                    directory = target.location.directory.casefold()
                    if directory in directories:
                        raise LocalSkillDuplicateError()
                except (LocalSkillDuplicateError, LocalSkillStorageError):
                    results = list(unattempted)
                    results[index] = package_result(
                        skill.package.name, skill.digest, PackagePublication.FAILED,
                    )
                    return tuple(results)
                directories.add(directory)
                targets.append(target)

            async def publish(
                target: _LocalSkillTarget, is_stopped: Callable[[], bool],
            ) -> LocalSkillPackageWriteResult:
                return await self._publish_batch_package(edit, destination, target, is_stopped)

            results = await dispatch_local_skill_batch(
                targets, unattempted, publish=publish,
                window=self._local_skill_parallelism if destination.supports_parallel_targets else 1,
            )
            if all(item.upload is not None for item in results) and not await self._package_runtime.is_destination_current(
                bot_id=edit.bot_id, owner_id=edit.owner_id, destination=destination,
            ):
                return tuple(replace(item, upload=None) for item in results)
            return results

    async def _publish_batch_package(
        self, edit: _LocalSkillEdit, destination: LocalSkillPackageDestination,
        target: _LocalSkillTarget, is_stopped: Callable[[], bool],
    ) -> LocalSkillPackageWriteResult:
        skill = target.skill
        name = skill.package.name
        publication = PackagePublication.NOT_DISPATCHED
        metadata = PackageMetadata.NOT_ATTEMPTED
        try:
            context = await self._package_runtime.resolve(bot_id=edit.bot_id, owner_id=edit.owner_id)
            if is_stopped():
                return package_result(name, skill.digest, PackagePublication.NOT_DISPATCHED)
            if not destination.matches(context):
                raise LocalSkillRuntimeUnavailableError()
            self._edit_guard.refresh(edit.lease)
            dispatch = self._skill_repo.reserve_local_skill_dispatch(
                bot_id=edit.bot_id, entity_id=edit.owner_id, binding_id=context.binding_id,
                device_id=context.device_id, name=name, digest=skill.digest,
            )
            if dispatch is None:
                # A stopped local await or a worker crash cannot establish that
                # the previous provider call no longer has write authority.
                return package_result(name, skill.digest, PackagePublication.UNKNOWN)
            runtime_result = None
            started = perf_counter()
            if destination.supports_package_apply:
                try:
                    runtime_result = await self._package_runtime.publish(
                        context, bot_id=edit.bot_id, skill_name=name,
                        layout=target.location.layout, package=skill.package.canonical_zip,
                    )
                except LocalSkillPackageOutcomeUnknownError:
                    return package_result(name, skill.digest, PackagePublication.UNKNOWN)
                except Exception:
                    self._skill_repo.finish_local_skill_dispatch(*dispatch)
                    raise
                publication = PackagePublication.PUBLISHED
                logger.info(
                    "Local Skill package published bot_id=%s skill_name=%s upload_ms=%.3f",
                    edit.bot_id, name, (perf_counter() - started) * 1000,
                )
                self._skill_repo.finish_local_skill_dispatch(*dispatch)
                self._edit_guard.refresh(edit.lease)
            else:
                # A legacy command can fail after writing a subset of the files.
                publication = PackagePublication.UNKNOWN
            started = perf_counter()
            metadata = PackageMetadata.UNKNOWN
            binding = RuntimeBindingTarget(
                edit.bot_id, str(edit.bot["owner_id"]), edit.owner_id, context.binding_id, context.device_id,
            )
            with expect_runtime_binding(binding):
                result = await self._persist_package(
                    edit, target, runtime_result, is_teclaw=destination.is_teclaw, deferred=True,
                )
            publication, metadata = PackagePublication.PUBLISHED, PackageMetadata.COMMITTED
            logger.info(
                "Local Skill package committed bot_id=%s skill_name=%s %s=%.3f",
                edit.bot_id, name,
                "metadata_ms" if destination.supports_package_apply else "legacy_upload_and_metadata_ms",
                (perf_counter() - started) * 1000,
            )
            started = perf_counter()
            await self._observe_package(edit, skill, result)
            logger.info(
                "Local Skill package observation stage bot_id=%s skill_name=%s enabled=%s observation_ms=%.3f",
                edit.bot_id, name, self._security_observation_enabled, (perf_counter() - started) * 1000,
            )
            if runtime_result is None:
                self._skill_repo.finish_local_skill_dispatch(*dispatch)
        except RuntimeBindingChangedError:
            return package_result(name, skill.digest, publication, PackageMetadata.FAILED)
        except Exception:
            logger.exception(
                "Local Skill batch package did not complete bot_id=%s skill_name=%s",
                edit.bot_id, name,
            )
            if publication is PackagePublication.NOT_DISPATCHED:
                publication = PackagePublication.FAILED
            return package_result(name, skill.digest, publication, metadata)
        return LocalSkillPackageWriteResult(
            LocalSkillPackageReceipt(name, skill.digest, publication, metadata), result, binding,
        )

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
        except RuntimeBindingChangedError:
            raise
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

    def _commit_runtime_replace(
        self,
        *,
        skill: dict[str, Any],
        owner_id: str,
        bot_id: str,
        actor_id: str,
        description: str,
        runtime_result: LocalSkillPackageRuntimeResult,
        deferred: bool,
        is_teclaw: bool,
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
                defer_skills_projection=deferred and not is_teclaw,
                record_tracked_projection=not deferred,
            )
            if replaced is None:
                raise RuntimeError("Local Skill metadata switch failed")
        except RuntimeBindingChangedError:
            raise
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
            return {
                "operation": "created",
                "skill": {**skill, "active": False},
                "actor_id": actor_id,
            }
        except (LocalSkillInvalidPackageError, LocalSkillDuplicateError, RuntimeBindingChangedError):
            raise
        except Exception as exc:  # details remain internal; public mapper is fixed
            # Compensation must continue after a failed rollback step: a failed
            # association delete must never prevent package cleanup.
            if skill is not None:
                try:
                    self._skill_repo.delete(skill["id"])
                except SkillAssetInUseError as conflict:
                    raise conflict from exc
                except Exception:
                    pass
            try:
                await storage.cleanup()
            except Exception:
                pass
            raise LocalSkillStorageError() from exc

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
        deferred: bool = False,
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
                new_locator=old_locator,
                description=description,
                defer_skills_projection=deferred and not is_teclaw,
                record_tracked_projection=not deferred,
            )
            if replaced is None:
                raise RuntimeError("Local Skill metadata switch failed")
            switched = True
        except RuntimeBindingChangedError:
            raise
        except Exception as exc:
            if switched or canonical_published:
                await self._restore_replacement(
                    skill=skill,
                    old_metadata=old_metadata,
                    staged=staged,
                    canonical=canonical,
                    backup=backup,
                    canonical_published=canonical_published,
                    switched=switched,
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
                    staged=staged,
                    canonical=canonical,
                    backup=backup,
                    canonical_published=canonical_published,
                    switched=switched,
                )
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
        }

    async def _restore_replacement(
        self,
        *,
        skill: dict[str, Any],
        old_metadata: dict[str, Any],
        staged,
        canonical,
        backup,
        canonical_published: bool,
        switched: bool,
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
        return self._device_context(bot_id=bot_id, owner_id=owner_id).provider == "teclaw"

    def _device_context(self, *, bot_id: str, owner_id: str) -> DeviceContext:
        try:
            return self._device_context_resolver_provider().resolve_for_bot(bot_id, owner_id)
        except Exception as exc:
            raise LocalSkillStorageError() from exc

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
                raise LocalSkillNotFoundError()
        return bot

    def _validate_zip(self, package: bytes) -> ValidatedSkillPackage:
        try:
            return self._package_validator.validate_legacy_local_zip(package)
        except SkillPackageInvalidError as exc:
            raise LocalSkillInvalidPackageError(exc.reason, exc.detail) from exc
        except SkillPackageTooLargeError as exc:
            raise LocalSkillTooLargeError(exc.reason) from exc

    def _pack_directory(self, files: Sequence[tuple[str, bytes]]) -> bytes:
        try:
            return self._package_validator.pack_directory(files)
        except SkillPackageInvalidError as exc:
            raise LocalSkillInvalidPackageError(exc.reason, exc.detail) from exc
        except SkillPackageTooLargeError as exc:
            raise LocalSkillTooLargeError(exc.reason) from exc
