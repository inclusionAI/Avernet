"""A NAS-rsync ``BotFileSyncPort`` — the openclaw family's workspace convergence.

The NAS-rsync family (``openclaw`` today, and the engines that share its
storage model: ``claude_code``, ``deepseek_harness``, ``hermes``) keeps a bot's
workspace on the NAS merge area (``/home/admin/.merge_nas/<storage_id>/.<engine>``).
A copy converges the source bot's tree onto the destination's by ``sudo rsync``
— the same primitive publish's ``_migrate_bot_instance`` uses, over the same
``get_bot_nas_dir`` paths, reading the system-level excludes off the same
``EngineBuildPlan.rsync_excludes`` the publish chain reads. That is the whole
point: one source of truth for *what a workspace sync looks like*, not a second
shipping of it for copy.

The port is engine-generic *within the family*: the engine root name and the
system excludes both come off the resolved build plan, so registering the same
strategy under a family member's engine_type is all the wiring a new NAS-rsync
engine needs. Fastdisk engines and teclaw live on different storage models and
have their own strategies (or the noop refusal, until they land). **The port
does not know which engines are in its family** — the registry resolving to it
is what says an engine runs NAS-rsync, and the W8 rule that a materialiser
never branches on an engine string is why it must not. The sandbox registry
refusing an unknown engine at ``resolve`` is the gate that keeps the same
strategy off a non-NAS engine the root did not register.

What the port does **not** touch: the content store, or any per-file fetch. A
sync is a path-to-path move; the destination's resource rows are reconciled by
whatever the engine's storage model already does.

The rsync runs as ``sudo`` (the publish migration's own privilege) and lands
files ``--chown=1000:1000``: the NAS merge area holds sandbox-owned files
(uid 1000 / mode 0600), and the destination container runs as uid 1000, so an
un-chowned ``sudo`` write would leave root-owned files the container cannot
read — the same reason publish's restore path chowns.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Callable

from agentclaw.community.core.repository.protocols.bot.bot import BotRepository
from agentclaw.community.core.workspace.engine_sandbox import (
    EngineSandboxRegistry,
)
from agentclaw.community.core.workspace.path_factory import WorkspacePathFactory
from agentclaw.community.log import get_logger
from agentclaw.community.core.ports.bot_file_sync_port import (
    BotFileSyncPort,
    BotFileSyncResult,
    BotFileSyncTarget,
)

logger = get_logger()

#: A failed sync never reaches the apply report as raw stderr. The one composed
#: reason the materialiser can stamp on an entry failure — the same discipline
#: the resources materialiser holds (no transport text, ever).
_SYNC_FAILED_REASON = "bot workspace sync failed"

# The uid:gid a NAS merge-area file belongs to: the container runs as uid 1000,
# so files a ``sudo`` rsync writes must be chowned back to it, or the container
# cannot read its own workspace. Mirrors publish's restore path (``--chown``).
_CHOWN = "1000:1000"


class NasBotFileSyncPort(BotFileSyncPort):
    """Converge a source bot's NAS workspace onto the destination's via rsync.

    Constructed by the composition root with the collaborator singletons the
    strategy needs — ``bot_repo`` to read the source bot's record, the
    ``sandbox_registry`` to resolve the engine's build plan (root name + system
    excludes), the ``path_factory`` for the NAS paths, and a ``run_command``
    callable that wraps ``subprocess`` (logging + error translation, the same
    shape publish's ``_run_local_command`` has). Construction holds no device
    graph and runs no command, so the thunk-at-boot discipline is kept.
    """

    def __init__(
        self,
        bot_repo: BotRepository,
        sandbox_registry: EngineSandboxRegistry,
        path_factory: WorkspacePathFactory,
        run_command: Callable[..., subprocess.CompletedProcess],
        env: str,
    ) -> None:
        self._bot_repo = bot_repo
        self._sandbox_registry = sandbox_registry
        self._path_factory = path_factory
        self._run_command = run_command
        self._env = env

    async def sync(
        self,
        *,
        target: BotFileSyncTarget,
        source_bot_id: str,
        source_owner_id: str,
        path: str,
        excludes: tuple[str, ...],
    ) -> BotFileSyncResult:
        """Converge the source bot's workspace tree onto the destination's."""
        # The literal ``path`` the copy serializer wrote (``workspace/``) is a
        # declared constant; the engine root is the real thing the strategy
        # syncs, so it is read off the build plan and ``path`` is unused here.
        del path

        engine_type = target.engine_type
        # Scope the lookup to the source owner: bot IDs are not globally unique.
        try:
            source = self._bot_repo.get_by_id_and_owner(source_bot_id, source_owner_id)
        except Exception:  # noqa: BLE001 — composed, never raw in the report
            logger.exception(
                "bot_file_sync.source_lookup_failed source_bot_id=%s engine=%s",
                source_bot_id, engine_type,
            )
            return BotFileSyncResult(ok=False, reason=_SYNC_FAILED_REASON)
        if source is None:
            return BotFileSyncResult(ok=False, reason="source bot not found")

        source_engine = source.get("active_engine") or source.get("engine_type")
        if source_engine != engine_type:
            # Copy preserves the source's engine onto a fresh bot of the same
            # engine; an unmatched pair is dirty data or a future cross-engine
            # feature this strategy does not serve.
            return BotFileSyncResult(
                ok=False, reason="cross-engine file sync is not supported"
            )

        # v1 same-env only: the source bot's NAS storage id keys on its env, and
        # ``get_current_env`` reads the *process* env. A source in another env
        # would resolve to a path that is not its own.
        if (source.get("env") or self._env) != target.env:
            return BotFileSyncResult(
                ok=False, reason="cross-env bot file sync is not supported"
            )

        try:
            build_plan = self._sandbox_registry.resolve(engine_type).get_build_plan(
                bot=source
            )
        except Exception:  # noqa: BLE001 — composed reason, never raw in report
            logger.exception(
                "bot_file_sync.resolve_build_plan_failed source_bot_id=%s engine=%s",
                source_bot_id, engine_type,
            )
            return BotFileSyncResult(ok=False, reason=_SYNC_FAILED_REASON)

        src_entity = source.get("entity_id") or source.get("owner_id") or ""
        src_type = source.get("entity_type") or "staff"
        source_path = self._path_factory.get_bot_nas_dir(
            src_entity, source_bot_id, engine_type, src_type
        ) / build_plan.source_root_name
        target_path = self._path_factory.get_bot_nas_dir(
            target.entity_id, target.bot_id, engine_type, target.entity_type
        ) / build_plan.source_root_name

        # No pre-flight ``exists()`` / ``mkdir()`` here: ``.merge_nas`` workspaces
        # are root-owned (the sandbox's own mount), so an ``os.stat`` /
        # ``mkdir`` from the backend's admin user raises ``PermissionError``
        # *before* rsync runs — the same paths publish's
        # ``_migrate_bot_instance`` reads, and the reason it skips the stat too.
        # rsync runs under ``sudo`` and reads/writes through the root-owned tree;
        # it creates the target directory on demand, and a missing source is a
        # clean non-zero rsync exit caught below — translated to
        # ``_SYNC_FAILED_REASON``, never a raw exception in the report.
        argv = [
            "sudo", "rsync", "-av",
            "--no-devices", "--no-specials",
            "--delete", "--delete-excluded",
            f"--chown={_CHOWN}",
            # System baseline ignore (sessions / .git / mcporter.json / …) read
            # off the same build plan publish reads — one source of truth.
            *(f"--exclude={rule}" for rule in build_plan.rsync_excludes),
            # The caller's manifest excludes, already governed literal-relative
            # paths by ``normalize_build_ignore_path``. Anchored to the source
            # root so the rule names a path, not a basename anywhere in the tree.
            *(f"--exclude=/{rule}" for rule in excludes),
            f"{source_path}/",
            f"{target_path}/",
        ]
        logger.info(
            "bot_file_sync.sync source=%s target=%s excludes=%s",
            source_path, target_path, len(excludes),
        )
        logger.info(
            "bot_file_sync.rsync_argv cmd=%s", argv,
        )
        try:
            self._run_command(
                cmd=argv,
                command_name="rsync bot file sync",
                error_message="bot workspace sync failed",
            )
        except Exception:  # noqa: BLE001 — composed, never raw in the report
            logger.warning(
                "bot_file_sync.rsync_failed source=%s target=%s", source_path, target_path,
            )
            return BotFileSyncResult(ok=False, reason=_SYNC_FAILED_REASON)
        return BotFileSyncResult(ok=True)


__all__ = ["NasBotFileSyncPort"]
