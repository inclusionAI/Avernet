"""Skill-center service factories.

These mint per-request skill services from injector-supplied
singletons. They live in ``core/`` (not ``di/modules/``) so that
``api/`` routes and other ``core/`` services can reference the factory
types without importing the DI wiring layer.

They are wired by :class:`SkillCenterModule` via explicit
``@provider`` methods (not ``binder.bind``): the providers pass the
runtime-keyed device dispatchers — which legitimately live in the DI
layer because they bridge to ``plugins`` — so this module only
needs the dispatcher *types* under ``TYPE_CHECKING``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, TYPE_CHECKING

from injector import inject

from agentclaw.community.core.bot_management.engines.registry import get_default_skill_set_selection_policy
from agentclaw.community.core.bot_management.services.engine_resolver import resolve_runtime_engine_for_bot
from agentclaw.community.core.config_compose.teclaw_paths import (
    to_local_skill_engine_path,
)
from agentclaw.community.core.devices.services.device_accessor import DeviceAccessor
from agentclaw.community.core.mcp.services.config_service import MCPConfigService
from agentclaw.community.core.mcp.services.sync_service import MCPSyncService
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillCategoryRepository,
)
from agentclaw.community.core.repository.protocols.skill_center import SkillRepository
from agentclaw.community.core.repository.protocols.skill_center import (
    SkillSetRepository,
)
from agentclaw.community.core.skill_center.capability_state_contract import (
    BotCapabilityStateReaderProtocol,
)
from agentclaw.community.core.skill_center.path_resolution import (
    build_pool_local_path_adapter,
)
from agentclaw.community.core.skill_center.services.git_sync import GitSyncService
from agentclaw.community.core.skill_center.services.skill_cache import MarketCache
from agentclaw.community.core.skill_center.services.skill_parameter_service import (
    SkillParameterService,
)
from agentclaw.community.core.skill_center.services.skill_service import SkillService
from agentclaw.community.core.skill_center.services.skill_set_service import (
    SkillSetService,
)
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.mcp_center import MCPCenterPlugin
from agentclaw.community.plugin_api.skill_repo_sync import SkillRepoSyncPlugin
from agentclaw.community.core.skill_center.skill_parameter_service_factory_protocol import SkillParameterServiceFactoryProtocol
from agentclaw.community.core.skill_center.skill_service_factory_protocol import SkillServiceFactoryProtocol
from agentclaw.community.core.skill_center.skill_set_service_factory_protocol import SkillSetServiceFactoryProtocol

if TYPE_CHECKING:
    # Runtime-keyed device dispatchers and the workspace path factory are
    # constructor *types* only. These factories are constructed via explicit
    # @provider methods, so the injector never introspects this class's type
    # hints at runtime — a TYPE_CHECKING import is sufficient for the tools
    # that do resolve annotations, and no importable boundary is crossed.
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
        DeviceFilesystemDispatcher,
    )
    from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
    from agentclaw.community.plugin_api.device_sync_dispatcher import (
        DeviceSyncDispatcher,
    )

logger = get_logger()


class LocalSkillQuarantineRepairError(OSError):
    """A partial authoritative-package delete could not be verified repaired."""


# Every package file is one device round trip. Issuing them sequentially made a
# package cost ``file_count × round_trip``, which dominates upload time for the many
# small files a skill package is made of. Fan them out instead — but bounded: device
# filesystems run their blocking transport through ``asyncio.to_thread``, whose
# default executor (``min(32, cpu_count + 4)`` threads) is shared with every other
# caller in the process, so an unbounded ``gather`` over a large package would starve
# unrelated work.
_PACKAGE_IO_CONCURRENCY = 8


async def _gather_package_io(coroutines: list) -> list:
    """Run package-file I/O concurrently, bounded, and drain before returning.

    Every coroutine is awaited to completion even after one fails, then the first
    failure *in input order* is re-raised. Draining is what makes the fan-out safe
    to substitute for the sequential loop: a caller that sees ``write`` fail treats
    the package as failed and immediately ``delete_tree``s its directory, so a write
    still in flight at that moment could land a file behind the cleanup and leave an
    orphan the next upload would trip over. Re-raising in input order keeps the
    surfaced error the same one the sequential loop would have raised.

    Cancellation is drained the same way, and needs its own handling because it does
    not travel the exception path: device filesystems block inside
    ``asyncio.to_thread``, which cannot interrupt a call already executing on a
    worker thread — cancelling only abandons the await while the HTTP write keeps
    going. Worse, ``CancelledError`` is a ``BaseException``, so
    ``LocalSkillUploadService``'s ``except Exception`` compensation is skipped while
    its ``finally`` still releases the edit lease. A retry could then acquire the
    lease, ``delete_tree`` the directory and start a fresh package that the
    abandoned writes land into. So the batch is shielded and drained before the
    cancellation is allowed to continue.
    """
    semaphore = asyncio.Semaphore(_PACKAGE_IO_CONCURRENCY)

    async def _bounded(coro):
        try:
            async with semaphore:
                return await coro
        finally:
            # A coroutine still queued on the semaphore when this batch is cancelled
            # would never be awaited, which Python surfaces as a RuntimeWarning.
            # Closing an already-finished coroutine is a no-op, so this is safe on
            # the normal path too.
            coro.close()

    batch = asyncio.ensure_future(
        asyncio.gather(*(_bounded(coro) for coro in coroutines), return_exceptions=True)
    )
    try:
        results = await asyncio.shield(batch)
    except BaseException:
        # Shielding keeps ``batch`` running when the caller is cancelled; awaiting it
        # here is what guarantees no write is still in flight once this raises.
        #
        # The drain is itself shielded, and loops, because cancellation can arrive
        # more than once — an aborted request overlapping with shutdown, say. A
        # plain ``await`` here would let that second cancel tear down ``batch``
        # itself and re-raise with the worker-thread writes still running, which is
        # exactly the failure the shield above prevents on the first cancel. Only
        # ``CancelledError`` is absorbed, and only until ``batch`` finishes, so this
        # delays cancellation by at most the batch's own remaining work.
        while not batch.done():
            try:
                await asyncio.shield(batch)
            except asyncio.CancelledError:
                continue
        raise
    for result in results:
        if isinstance(result, BaseException):
            raise result
    return results


class LocalSkillPackageStorage:
    """Explicit package-I/O port owned by the SkillService factory."""

    def __init__(self, filesystem, device_directory: str) -> None:
        self._filesystem = filesystem
        self._device_directory = device_directory

    @property
    def directory(self) -> str:
        """The internal device locator this storage instance owns."""
        return self._device_directory

    async def write(self, files: list[tuple[str, bytes]]) -> None:
        await _gather_package_io(
            [
                self._filesystem.write_file(
                    f"{self._device_directory}/{relative_path}", content
                )
                for relative_path, content in files
            ]
        )

    async def prepare(self) -> None:
        """Remove an orphaned failed upload before writing a first package."""
        if not await self._filesystem.exists(self._device_directory):
            return
        if not await self._filesystem.delete_tree(self._device_directory):
            raise OSError("unable to clear prior Local Skill upload")

    async def cleanup(self) -> bool:
        return await self._filesystem.delete_tree(self._device_directory)

    async def exists(self) -> bool:
        """Whether this storage currently has an authoritative package."""
        return await self._filesystem.exists(self._device_directory)

    async def read_file(self, relative_path: str) -> bytes | None:
        """Read one validated package-relative file without exposing its locator."""
        if (
            not relative_path
            or relative_path.startswith("/")
            or ".." in relative_path.split("/")
        ):
            raise ValueError("Local Skill package path is invalid")
        return await self._filesystem.read_file(
            f"{self._device_directory}/{relative_path}"
        )

    async def verify(self) -> bool:
        """Read and validate every package file without changing storage."""
        await self._read_package_files()
        return True

    async def read_package_files(self) -> list[tuple[str, bytes]]:
        """The installed package's bytes, read back the way verify/copy_to read
        them: every listed path validated before a single read is issued.

        The public alias of the private reader those flows share — a caller
        that needs the authoritative bytes (not just a boolean) reads the
        same thing the bot is actually running, with no new traversal or
        locator knowledge of its own.
        """
        return await self._read_package_files()

    async def copy_to(
        self, target: "LocalSkillPackageStorage", *, replace: bool = False
    ) -> None:
        """Copy and verify this complete package without deleting the source.

        Device backends deliberately do not promise a common directory rename.
        Replacement therefore stages and verifies bytes first, then publishes
        them to the stable user-visible package directory through this portable
        copy seam.  The source remains available for rollback until the caller
        explicitly cleans it up.
        """
        files = await self._read_package_files()
        if await target._filesystem.exists(target.directory):
            if not replace:
                raise OSError("Local Skill copy target already exists")
            if not await target.cleanup():
                raise OSError("unable to clear Local Skill copy target")
        if not await target._restore_contents(files):
            raise OSError("Local Skill copy verification failed")

    async def quarantine_to(self, quarantine: "LocalSkillPackageStorage") -> None:
        """Copy, verify, then remove this authoritative package.

        Device backends have no shared directory-rename operation.  This
        portable sequence deliberately retains the authoritative source until
        every copied file has been read back byte-for-byte from quarantine.
        """
        files = await self._read_package_files()
        if await quarantine._filesystem.exists(quarantine.directory):
            raise OSError("Local Skill quarantine already exists")
        await _gather_package_io(
            [
                quarantine._filesystem.write_file(
                    f"{quarantine.directory}/{relative_path}", content
                )
                for relative_path, content in files
            ]
        )
        copied = await _gather_package_io(
            [
                quarantine._filesystem.read_file(
                    f"{quarantine.directory}/{relative_path}"
                )
                for relative_path, _ in files
            ]
        )
        if any(
            actual != expected
            for actual, (_, expected) in zip(copied, files)
        ):
            raise OSError("Local Skill quarantine verification failed")
        try:
            source_cleaned = await self.cleanup()
        except Exception:
            # A device backend can raise after partially deleting source
            # bytes. Treat that exactly like a failed cleanup so the verified
            # quarantine copy is used to restore the authoritative package.
            source_cleaned = False
        if not source_cleaned:
            try:
                restored = await self._restore_contents(files)
            except Exception as exc:
                raise LocalSkillQuarantineRepairError(
                    "Local Skill package repair failed"
                ) from exc
            if not restored:
                raise LocalSkillQuarantineRepairError(
                    "Local Skill package repair verification failed"
                )
            raise OSError("Local Skill package quarantine failed")

    async def restore_from(
        self, quarantine: "LocalSkillPackageStorage"
    ) -> tuple[bool, bool]:
        """Verify or restore the package before purging its quarantine copy."""
        files = await quarantine._read_package_files()
        if await self._filesystem.exists(self.directory):
            try:
                source_files = await self._read_package_files()
            except OSError:
                source_files = []
            if source_files == files:
                return True, await quarantine.cleanup()
            if not await self.cleanup():
                return False, False
        if not await self._restore_contents(files):
            return False, False
        return True, await quarantine.cleanup()

    async def _restore_contents(self, files: list[tuple[str, bytes]]) -> bool:
        await _gather_package_io(
            [
                self._filesystem.write_file(
                    f"{self.directory}/{relative_path}", content
                )
                for relative_path, content in files
            ]
        )
        restored = await _gather_package_io(
            [
                self._filesystem.read_file(f"{self.directory}/{relative_path}")
                for relative_path, _ in files
            ]
        )
        return all(
            actual == expected
            for actual, (_, expected) in zip(restored, files)
        )

    async def _read_package_files(self) -> list[tuple[str, bytes]]:
        entries = await self._filesystem.list_dir(
            self._device_directory, recursive=True
        )
        if entries is None:
            raise OSError("Local Skill package is missing")
        relative_paths: list[str] = []
        for entry in entries:
            if entry.get("is_dir"):
                continue
            relative_path = str(entry.get("relative_path") or "")
            if (
                not relative_path
                or relative_path.startswith("/")
                or ".." in relative_path.split("/")
            ):
                raise OSError("Local Skill package has an invalid file path")
            relative_paths.append(relative_path)
        # Every listed path is validated before a single read is issued, so a
        # package containing an invalid path is still rejected outright rather
        # than partially read.
        contents = await _gather_package_io(
            [
                self._filesystem.read_file(
                    f"{self._device_directory}/{relative_path}"
                )
                for relative_path in relative_paths
            ]
        )
        files: list[tuple[str, bytes]] = []
        for relative_path, content in zip(relative_paths, contents):
            if content is None:
                raise OSError("Local Skill package file disappeared")
            files.append((relative_path, content))
        if not files:
            raise OSError("Local Skill package is empty")
        return files


class SkillServiceFactory(SkillServiceFactoryProtocol):
    """Mints :class:`SkillService` instances scoped to per-request paths.

    Holds the @inject-supplied singletons (``skill_repo``,
    ``skill_repo_sync``, ``category_repo``, ``member_repo``);
    ``create()`` forwards the caller's request-scoped paths and merges in
    the held deps.
    """

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        bot_repo: BotRepository,
        skill_repo_sync: SkillRepoSyncPlugin,
        category_repo: SkillCategoryRepository,
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
        market_cache: MarketCache,
        git_sync_service_factory: Callable[[], GitSyncService],
        path_factory: "WorkspacePathFactory",
        pool_layout_paths: Callable[
            [str, str, str],
            tuple[str, str, str] | None,
        ],
    ) -> None:
        self._skill_repo = skill_repo
        self._bot_repo = bot_repo
        self._skill_repo_sync = skill_repo_sync
        self._category_repo = category_repo
        self._device_fs_dispatcher = device_fs_dispatcher
        self._market_cache = market_cache
        self._git_sync_service_factory = git_sync_service_factory
        self._path_factory = path_factory
        self._pool_layout_paths = pool_layout_paths

    def resolve_pool_paths(
        self,
        entity_id: str,
        bot_id: str,
        engine_type: str,
    ) -> tuple[str, str, str] | None:
        """Resolve canonical Pool paths for a Bot when its layout owns IO."""

        return self._pool_layout_paths(entity_id, bot_id, engine_type)

    def create(
        self,
        active_dir: Optional[Path] = None,
        repo_dir: Optional[Path] = None,
        local_dir: Optional[Path] = None,
        global_repo_dir: Optional[Path] = None,
        device_fs_factory=None,
        local_skill_path_adapter: Optional[Callable[[str], str]] = None,
        local_skill_locator_adapter: Optional[Callable[[str], str]] = None,
        entity_id: str | None = None,
        bot_owner_id: str | None = None,
        bot_id: str | None = None,
        engine_type: str | None = None,
        runtime_uses_pool_paths: bool = False,
        device_owner_id: str | None = None,
    ) -> SkillService:
        uses_pool_paths = runtime_uses_pool_paths
        if entity_id is not None and bot_id is not None:
            # Paths are scoped by the Bot entity, while Bot lookup and device
            # binding are owned by ac_bots.owner_id.  They differ for project
            # and team Bots and therefore must not be conflated.
            lookup_owner_id = bot_owner_id or entity_id
            runtime_engine = resolve_runtime_engine_for_bot(
                str(bot_id),
                str(lookup_owner_id),
                override=engine_type,
                bot_repo=self._bot_repo,
            )
            pool_paths = self.resolve_pool_paths(
                str(lookup_owner_id),
                str(bot_id),
                runtime_engine or "",
            )
            if pool_paths is not None:
                uses_pool_paths = True
                active_path, local_path, repo_path = pool_paths
                active_dir = Path(active_path)
                local_dir = Path(local_path)
                repo_dir = Path(repo_path)
                pool_local_adapter = build_pool_local_path_adapter(local_dir)
                local_skill_path_adapter = pool_local_adapter
                local_skill_locator_adapter = pool_local_adapter


        return SkillService(
            skill_repo=self._skill_repo,
            skill_repo_sync=self._skill_repo_sync,
            category_repo=self._category_repo,
            market_cache=self._market_cache,
            active_dir=active_dir,
            repo_dir=repo_dir,
            local_dir=local_dir,
            global_repo_dir=global_repo_dir,
            device_fs_factory=device_fs_factory or self._device_fs_dispatcher.for_bot,
            git_sync_service_factory=self._git_sync_service_factory,
            local_skill_path_adapter=local_skill_path_adapter,
            local_skill_locator_adapter=local_skill_locator_adapter,
            runtime_uses_pool_paths=uses_pool_paths,
            device_owner_id=device_owner_id or bot_owner_id or entity_id,
        )

    def local_skill_package_storage(
        self,
        *,
        entity_id: str,
        owner_id: str,
        bot_id: str,
        engine_type: str | None,
        entity_type: str,
        is_desktop: bool,
        is_teclaw: bool,
        name: str,
        directory_name: str | None = None,
    ) -> tuple[str, LocalSkillPackageStorage]:
        """Return a Bot-local package storage port.

        ``directory_name`` is intentionally internal.  A replacement writes a
        complete package to an isolated versioned directory, then publishes it
        to the stable ``name`` directory.  Metadata never points at the
        internal directory.
        """
        service = self.create(
            entity_id=entity_id,
            bot_owner_id=owner_id,
            bot_id=bot_id,
            engine_type=engine_type,
        )
        local_dir = service.local_dir
        if not service.runtime_uses_pool_paths:
            runtime_engine = resolve_runtime_engine_for_bot(
                bot_id,
                owner_id,
                override=engine_type,
                bot_repo=self._bot_repo,
            )
            local_dir = self._path_factory.get_bot_skills_local_dir(
                entity_id,
                bot_id,
                runtime_engine or "openclaw",
                entity_type,
                is_desktop=is_desktop,
                is_teclaw=is_teclaw,
            )
        directory = str(local_dir / (directory_name or name))
        local_skill_path_adapter = service._local_skill_path_adapter
        if is_teclaw and not service.runtime_uses_pool_paths:
            local_skill_path_adapter = to_local_skill_engine_path
        return directory, LocalSkillPackageStorage(
            service._device_fs_factory(bot_id, owner_id),
            local_skill_path_adapter(directory),
        )

    def local_skill_package_storage_for_locator(
        self,
        *,
        entity_id: str,
        owner_id: str,
        bot_id: str,
        engine_type: str | None,
        entity_type: str,
        is_desktop: bool,
        is_teclaw: bool,
        locator: str,
    ) -> LocalSkillPackageStorage:
        """Re-open an existing internal Local package locator for cleanup."""
        service = self.create(
            entity_id=entity_id,
            bot_owner_id=owner_id,
            bot_id=bot_id,
            engine_type=engine_type,
        )
        local_dir = service.local_dir
        if not service.runtime_uses_pool_paths:
            runtime_engine = resolve_runtime_engine_for_bot(
                bot_id,
                owner_id,
                override=engine_type,
                bot_repo=self._bot_repo,
            )
            local_dir = self._path_factory.get_bot_skills_local_dir(
                entity_id,
                bot_id,
                runtime_engine or "openclaw",
                entity_type,
                is_desktop=is_desktop,
                is_teclaw=is_teclaw,
            )
        resolved_locator = Path(locator)
        if not resolved_locator.is_absolute():
            if resolved_locator.parts[:1] == (local_dir.name,):
                resolved_locator = local_dir.parent / resolved_locator
            else:
                resolved_locator = local_dir / resolved_locator
        resolved_base = local_dir.resolve()
        resolved_candidate = resolved_locator.resolve()
        try:
            relative_locator = resolved_candidate.relative_to(resolved_base)
        except ValueError as exc:
            raise ValueError(
                "Local Skill cleanup locator escapes skills-local"
            ) from exc
        if not relative_locator.parts:
            raise ValueError("Local Skill cleanup locator must name a package")
        # Keep the original path form for the device adapter (notably Teclaw's
        # relative ``skills-local`` namespace), after lexical containment has
        # been proven against an absolute normalized base.
        resolved_locator = local_dir / relative_locator
        local_skill_path_adapter = service._local_skill_path_adapter
        if is_teclaw and not service.runtime_uses_pool_paths:
            local_skill_path_adapter = to_local_skill_engine_path
        return LocalSkillPackageStorage(
            service._device_fs_factory(bot_id, owner_id),
            local_skill_path_adapter(str(resolved_locator)),
        )


class SkillSetServiceFactory(SkillSetServiceFactoryProtocol):
    """Mints :class:`SkillSetService` instances per-request.

    ``resolver`` / ``device_sync_dispatcher`` 走 lazy ``Callable`` thunk 注入,
    防止构造期 DI 循环:
      ``BotService → SkillSetServiceFactory → DeviceContextResolver
      → ArcaConnInfoBuilder → DeviceService → BotService``。
    """

    @inject
    def __init__(
        self,
        skill_repo: SkillRepository,
        skill_set_repo: SkillSetRepository,
        mcp_center: MCPCenterPlugin,
        mcp_config_service: MCPConfigService,
        skill_service_factory: SkillServiceFactory,
        resolver_provider: "Callable[[], DeviceContextResolver]",
        device_sync_dispatcher_provider: "Callable[[], DeviceSyncDispatcher]",
        mcp_sync_service: MCPSyncService,
        bot_repo: BotRepository,
        device_plugin: DeviceAccessor,
        path_factory: "WorkspacePathFactory",
        pool_layout_paths: Callable[
            [str, str, str],
            tuple[str, str, str] | None,
        ],
        ext_info_provider: Callable[[str], Mapping[str, Any] | None] | None = None,
        reader: BotCapabilityStateReaderProtocol | None = None,
    ) -> None:
        self._skill_repo = skill_repo
        self._skill_set_repo = skill_set_repo
        self._mcp_center = mcp_center
        self._mcp_config_service = mcp_config_service
        self._skill_service_factory = skill_service_factory
        self._resolver_provider = resolver_provider
        self._device_sync_dispatcher_provider = device_sync_dispatcher_provider
        self._mcp_sync_service = mcp_sync_service
        self._bot_repo = bot_repo
        self._device_plugin = device_plugin
        self._path_factory = path_factory
        self._pool_layout_paths = pool_layout_paths
        self._ext_info_provider = ext_info_provider
        self._reader = reader

    def initialize_installations(
        self,
        *,
        bot_id: str,
        owner_id: str,
        bot: Mapping[str, Any] | None = None,
    ) -> None:
        """Initialize one persisted Bot's DB-only Installation facts.

        This keeps Bot lifecycle services on the existing Skill Center factory
        seam. A missing reader is a construction error, never a successful
        no-op that leaves a created Bot without its capability projection.
        """
        if self._reader is None:
            raise RuntimeError("SkillSetServiceFactory requires a capability reader")
        self._reader.initialize_installations(
            bot_id=bot_id, owner_id=owner_id, bot=bot
        )

    def create(
        self,
        skills_dir: Optional[Path] = None,
        repo_dir: Optional[Path] = None,
        local_dir: Optional[Path] = None,
        user_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        bot_id: Optional[str] = None,
        engine_type: Optional[str] = None,
        runtime_engine_type: Optional[str] = None,
        entity_type: Optional[str] = None,
    ) -> SkillSetService:
        # Build a SkillSetService first to compute the resolved paths,
        # then mint a SkillService scoped to those paths and attach it.
        # We need the resolved paths to construct the SkillService; so
        # we do a two-step: construct a "draft" service to get paths,
        # then mint the SkillService and overwrite. Simpler: replicate
        # path resolution inline.
        # ── Resolve paths the same way SkillSetService does ──
        from agentclaw.community.core.skill_center.services.skill_set_service import (
            SKILLS_DIR,
            SKILLS_LOCAL_DIR,
            SKILLS_REPO_DIR,
            _get_bot_paths,
        )

        is_desktop = False
        if user_id or entity_id:
            try:
                owner_id = user_id or entity_id
                bot = self._bot_repo.get_by_id_and_owner(bot_id or "default", owner_id)
                is_desktop = bool(bot and bot.get("bot_type") == "desktop")
            except Exception as exc:
                logger.warning(
                    "[SkillSetServiceFactory] bot_type lookup failed for "
                    "bot_id=%s owner_id=%s: %s — defaulting is_desktop=False",
                    bot_id or "default",
                    owner_id,
                    exc,
                )
        # Backward compatibility: callers that explicitly pass only ``engine_type``
        # to this factory have historically meant "use this engine's filesystem
        # layout". HTTP routers that need bot-aware runtime remapping now pass
        # ``runtime_engine_type`` explicitly, so keep this fallback predictable.
        effective_runtime_engine = runtime_engine_type or engine_type
        if effective_runtime_engine is None:
            owner_id = user_id or entity_id
            effective_runtime_engine = resolve_runtime_engine_for_bot(
                bot_id or "default",
                str(owner_id) if owner_id is not None else None,
                bot_repo=self._bot_repo,
            )

        if user_id or entity_id:
            resolved_skills, resolved_repo, resolved_local = _get_bot_paths(
                path_factory=self._path_factory,
                user_id=user_id,
                entity_id=entity_id,
                bot_id=bot_id,
                engine_type=effective_runtime_engine,
                entity_type=entity_type,
                is_desktop=is_desktop,
            )
        else:
            resolved_skills = skills_dir or SKILLS_DIR
            resolved_repo = repo_dir or SKILLS_REPO_DIR
            resolved_local = local_dir or SKILLS_LOCAL_DIR
        effective_owner = user_id or entity_id
        local_skill_path_adapter = None
        if effective_owner is not None and bot_id is not None:
            pool_paths = self._pool_layout_paths(
                str(effective_owner),
                str(bot_id),
                effective_runtime_engine or engine_type or "",
            )
            if pool_paths is not None:
                active_path, local_path, repo_path = pool_paths
                resolved_skills = Path(active_path)
                resolved_local = Path(local_path)
                resolved_repo = Path(repo_path)
                local_skill_path_adapter = build_pool_local_path_adapter(resolved_local)

        skill_service = self._skill_service_factory.create(
            active_dir=resolved_skills,
            repo_dir=resolved_repo,
            local_dir=resolved_local,
            local_skill_path_adapter=local_skill_path_adapter,
            # The resolved directories alone are insufficient: SkillService
            # needs this flag to retain Pool-specific activation and cleanup
            # semantics without resolving the same layout a second time.
            runtime_uses_pool_paths=local_skill_path_adapter is not None,
            device_owner_id=effective_owner,
        )

        return SkillSetService(
            skill_repo=self._skill_repo,
            skill_set_repo=self._skill_set_repo,
            mcp_center=self._mcp_center,
            mcp_config_service=self._mcp_config_service,
            skill_service=skill_service,
            bot_repo=self._bot_repo,
            skills_dir=skills_dir,
            repo_dir=repo_dir,
            local_dir=local_dir,
            user_id=user_id,
            entity_id=entity_id,
            bot_id=bot_id,
            engine_type=engine_type,
            runtime_engine_type=effective_runtime_engine,
            entity_type=entity_type,
            resolver=self._resolver_provider(),
            device_sync_dispatcher=self._device_sync_dispatcher_provider(),
            mcp_sync_service=self._mcp_sync_service,
            device_plugin=self._device_plugin,
            ext_info_provider=self._ext_info_provider,
            default_skill_set_selection_policy=get_default_skill_set_selection_policy(),
            path_factory=self._path_factory,
            pool_layout_paths=self._pool_layout_paths,
            reader=self._reader,
        )


class SkillParameterServiceFactory(SkillParameterServiceFactoryProtocol):
    """Mints :class:`SkillParameterService` for a (bot_id, user_id) pair.

    Computes the per-call ``device_fs`` via ``DeviceContextResolver`` +
    ``DeviceFilesystemDispatcher.dispatch(ctx)``, then constructs the
    service. Holds no singletons of its own — every dep is computed per
    call — but still lives as a ``@singleton`` for symmetry with the
    other factories.
    """

    @inject
    def __init__(
        self,
        resolver: "DeviceContextResolver",
        device_fs_dispatcher: "DeviceFilesystemDispatcher",
    ) -> None:
        self._resolver = resolver
        self._device_fs_dispatcher = device_fs_dispatcher

    async def create(
        self,
        bot_id: str,
        user_id: str,
        load_on_init: bool = True,
    ) -> SkillParameterService:
        ctx = self._resolver.resolve_for_bot(bot_id, user_id)
        device_fs = self._device_fs_dispatcher.dispatch(ctx)
        # teclaw does not use skill_parameters.json (the engine neither owns nor
        # consumes it), so disable engine read/write for it — load/save become
        # no-ops. arca/baas/local keep reading/writing the engine-absolute default.
        engine_io_enabled = ctx.provider != "teclaw"
        service = SkillParameterService(
            device_fs=device_fs, engine_io_enabled=engine_io_enabled
        )
        if load_on_init:
            await service.async_load()
        return service
