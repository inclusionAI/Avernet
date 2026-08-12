"""``DeviceCredentialsAdminsWriter`` — seed/sync the ``ADMINS=`` line of a
running container's ``/home/admin/.credentials``.

Two entry points share one read-modify-write:

* :meth:`seed_for_publish` — called from the teclaw publish handler once the
  online container is up. Takes the **online** ``binding_id`` the handler already
  holds (it polled that binding to SUCCESS) and resolves it via
  ``resolve_for_binding``. Transport / binding errors **propagate** so the
  durable task can ``Retry`` (publish-seed is load-bearing; a silent skip would
  strand the container without admins).
* :meth:`sync_on_change` — called from ``CollaboratorService.on_collaboration_changed``
  on every collaborator add/update/remove. For a service bot it resolves the
  bot's **online** binding from the publish record's ``ext.binding.online``
  (``ac_bots.binding_id`` is the draft for service bots, so ``resolve_for_bot``
  would mis-resolve — see ``device_context_resolver.resolve_for_binding``
  docstring); for bots without a publish record it falls back to
  ``resolve_for_bot`` (today's ARCA/personal/desktop behavior). It **swallows**
  every error (warns) — a collaborator change must never break the main flow.

The engine reads admins from ``.credentials`` and the file already exists in a
running container (engine-created at boot with TOKEN/CLIENT_ID/etc.), so the
writer **read-modify-writes** the existing file (preserving every other line)
and never creates a bare ADMINS-only file (the engine rejects one). The engine
hot-reloads ``.credentials``, so a runtime write takes effect immediately.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, List

from agentclaw.community.core.bot_collaborator.models import CollaboratorRole
from agentclaw.community.core.devices.services.device_context import (
    DeviceContext,
    DeviceNotBoundError,
    UnknownProviderError,
)
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

if TYPE_CHECKING:
    from agentclaw.community.core.bot_collaborator.models import CollaboratorRecord
    from agentclaw.community.core.repository.protocols.bot import (
        CollaboratorRepositoryProtocol,
    )
    from agentclaw.community.core.repository.protocols.publishing import (
        BotPublishRepositoryProtocol,
    )
    from agentclaw.community.core.devices.services.device_context_resolver import (
        DeviceContextResolver,
    )
    from agentclaw.community.core.devices.services.device_filesystem_dispatcher import (
        DeviceFilesystemDispatcher,
    )
    from agentclaw.community.core.devices.services.device_filesystem import (
        DeviceFileSystem,
    )

logger = get_logger()

# 运行设备上 .credentials 的固定路径(prod 容器内 start_service.sh 写入处,
# 与 engine ``CredentialsService.DEFAULT_CREDENTIALS_PATH`` 一致)。
_DEVICE_CREDENTIALS_PATH = "/home/admin/.credentials"


def _run_coro_blocking(coro: Any) -> Any:
    """在独立线程里跑协程并阻塞等待结果。

    ``on_collaboration_changed`` / publish handler 调本 writer 时运行在事件循环
    线程上(同步入口)。此处:
      - 不能用 ``asyncio.run``(当前线程已有 running loop → 抛错);
      - 不能用 ``run_coroutine_threadsafe(coro, 当前loop)``(当前线程被阻塞 → 死锁)。
    所以起一个新线程、用全新的 ``asyncio.run`` 跑,再 ``join`` 取结果/异常。
    """
    import asyncio

    from agentclaw.community.utils.avernet_tenant import bind_current_avernet_tenant

    box: Dict[str, Any] = {}

    def _runner() -> None:
        try:
            box["result"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001 - 透传给调用线程统一处理
            box["error"] = e

    thread = threading.Thread(
        target=bind_current_avernet_tenant(_runner), daemon=True
    )
    thread.start()
    thread.join()

    if "error" in box:
        raise box["error"]
    return box.get("result")


def _replace_admins_line(content: str, admins: List[str]) -> str:
    """逐行替换/追加 ``ADMINS=`` 行，其它行原样保留。

    镜像 engine ``CredentialsService.update_fields`` 的逐行替换思路(不引入 engine 依赖)。
    ``admins`` 为空 → 写 ``ADMINS=``(空值)以清空，而非删除行——保证设备端能读到“无 admin”。
    """
    admins_value = ",".join(admins)
    new_line = f"ADMINS={admins_value}"

    lines = content.splitlines()
    replaced = False
    out: List[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.partition("=")[0].strip().upper()
            if key == "ADMINS":
                out.append(new_line)
                replaced = True
                continue
        out.append(line)

    if not replaced:
        out.append(new_line)

    # 保持末尾换行(与 ``_write_credentials`` 写出的文件一致)。
    return "\n".join(out) + "\n"


async def _rewrite_credentials_admins(
    fs: "DeviceFileSystem", admins: List[str]
) -> None:
    """read-modify-write 设备 ``.credentials``，只替换/追加 ``ADMINS=`` 行。

    文件不存在(``read_file`` 返回 ``None``) → 跳过：不凭空造文件。引擎拒绝只含
    ``ADMINS=`` 的 ``.credentials``(会丢 TOKEN/CLIENT_ID 等启动字段)，而运行容器里
    该文件由引擎在 boot 时创建，理应存在;真不存在属异常态,记日志不写。
    """
    raw = await fs.read_file(_DEVICE_CREDENTIALS_PATH)
    if raw is None:
        logger.info(
            "[credentials_admins_writer] %s 不存在，跳过(不创建)",
            _DEVICE_CREDENTIALS_PATH,
        )
        return

    new_content = _replace_admins_line(raw.decode("utf-8"), admins)
    await fs.write_file(_DEVICE_CREDENTIALS_PATH, new_content.encode("utf-8"))


class DeviceCredentialsAdminsWriter:
    """Write the ``ADMINS=`` line of a running container's ``.credentials``.

    Resolves the **online** binding for service bots (via the publish record's
    ``ext.binding.online``); falls back to ``resolve_for_bot`` for bots without a
    publish record. Read-modify-write only — never creates a bare file.
    """

    def __init__(
        self,
        *,
        collaborator_repo: "CollaboratorRepositoryProtocol",
        bot_publish_repo: "BotPublishRepositoryProtocol",
        resolver_provider: "Callable[[], DeviceContextResolver]",
        device_fs_dispatcher_provider: "Callable[[], DeviceFilesystemDispatcher]",
    ) -> None:
        self._collaborator_repo = collaborator_repo
        self._bot_publish_repo = bot_publish_repo
        self._resolver_provider = resolver_provider
        self._device_fs_dispatcher_provider = device_fs_dispatcher_provider

    # ── public entry points ────────────────────────────────────────────────

    def seed_for_publish(self, binding_id: int, bot_id: str, owner_id: str) -> None:
        """Seed ``ADMINS=`` into the ONLINE container right after publish.

        ``binding_id`` is the online binding the publish handler just polled to
        SUCCESS; admins are read from the collaborator repo (current env,
        ``role=admin``). Errors **propagate**: the handler turns a transport
        failure into ``Retry`` and a missing binding into ``Complete``.
        """
        admins = self._query_admins(bot_id, owner_id)
        self._write_for_binding(binding_id, bot_id, owner_id, admins)

    def sync_on_change(self, bot_id: str, owner_id: str, admins: List[str]) -> None:
        """Update the running container's ``ADMINS=`` on a collaborator change.

        Service bot → resolve the online binding (``ext.binding.online``) and
        write via ``resolve_for_binding``; otherwise fall back to
        ``resolve_for_bot`` (ARCA/personal/desktop). All errors are swallowed
        (warned) — a collaborator change must never break the main flow.
        """
        try:
            online_binding_id = self._resolve_online_binding_id(bot_id)
            if online_binding_id is not None:
                self._write_for_binding(online_binding_id, bot_id, owner_id, admins)
            else:
                self._write_for_bot(bot_id, owner_id, admins)
        except (DeviceNotBoundError, UnknownProviderError) as e:
            logger.info(
                "[credentials_admins_writer] bot 无运行设备，跳过 credentials 同步: "
                "bot_id=%s, reason=%s",
                bot_id, e,
            )
        except Exception as e:  # noqa: BLE001 - 协作者变更主流程不可因写 .credentials 失败而中断
            logger.warning(
                "[credentials_admins_writer] 同步 .credentials 失败(已忽略): "
                "bot_id=%s, error=%s",
                bot_id, e,
            )

    # ── internals ──────────────────────────────────────────────────────────

    def _query_admins(self, bot_id: str, owner_id: str) -> List[str]:
        env = get_current_env()
        collaborators: List["CollaboratorRecord"] = self._collaborator_repo.list_by_bot(
            bot_id=bot_id, owner_id=owner_id, env=env, role=CollaboratorRole.ADMIN,
        )
        return [c.user_id for c in collaborators]

    def _resolve_online_binding_id(self, bot_id: str) -> int | None:
        """Read the bot's online binding id from its latest success publish record."""
        env = get_current_env()
        record = self._bot_publish_repo.get_latest_success_by_source_bot_id(bot_id, env)
        if record is None:
            return None
        return (record.ext or {}).get("binding", {}).get("online")

    def _write_for_binding(
        self, binding_id: int, bot_id: str, owner_id: str, admins: List[str]
    ) -> None:
        ctx = self._resolver_provider().resolve_for_binding(
            binding_id, owner_id, bot_id=bot_id
        )
        self._do_write(ctx, admins)

    def _write_for_bot(self, bot_id: str, owner_id: str, admins: List[str]) -> None:
        ctx = self._resolver_provider().resolve_for_bot(bot_id, owner_id)
        self._do_write(ctx, admins)

    def _do_write(self, ctx: DeviceContext, admins: List[str]) -> None:
        fs = self._device_fs_dispatcher_provider().dispatch(ctx)
        _run_coro_blocking(_rewrite_credentials_admins(fs, admins))
        logger.info(
            "[credentials_admins_writer] synced ADMINS to .credentials: "
            "bot_id=%s provider=%s admins=%s",
            ctx.bot_id, ctx.provider, admins,
        )
