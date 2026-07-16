"""Prod-mode NotifyBotLister.

走 ``DeviceBindingRepository``——这是唯一能稳定拿到 sandbox_id 的路径，
与 ``ProdHealthProbe.bots_health`` / ``probe_engine_prod`` 完全一致:

  - ``entity_type="staff"`` —— 真实表里 owner 是 staff,不是 "USER"
  - ``env=get_current_env()`` —— 防跨环境串数据
  - ``status="ACTIVE"`` —— 只取活绑定
  - ``sandbox_id`` 回退到 ``binding.device_id`` —— 老版本绑定没写 device_props

bot 名字仍然从 ``BotRepository`` 解析 (device_props.bolt_id → bot_id)。

协作者机器人：除 owner 自己的活绑定外，再查 ``CollaboratorRepository``,
把当前用户作为协作者参与的 Bot 一并纳入通知列表（对齐
``/api/bots/by-owner-or-collaborator`` 的语义）。协作者机器人的设备绑定
归属 owner，因此通过 ``get_active_by_bot_and_owner(bot_id, owner_id)``
取 owner 的 active binding 拿 sandbox。与 owner 路径一致：没有 active
绑定 / 取不到 sandbox 的协作者机器人会被跳过（无法探活 engine）。
"""
from __future__ import annotations

from agentclaw.community.core.bot_collaborator.repository.protocol import (
    CollaboratorRepositoryProtocol,
)
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.log import get_logger
from agentclaw.community.utils.env_utils import get_current_env

logger = get_logger()


class RepositoryNotifyBotLister:

    def __init__(
        self,
        binding_repo: DeviceBindingRepository,
        bot_repo: BotRepository,
        collaborator_repo: CollaboratorRepositoryProtocol | None = None,
    ) -> None:
        self._binding_repo = binding_repo
        self._bot_repo = bot_repo
        # 协作者来源可选：老 profile / 未装配 BotCollaboratorModule 时回退到
        # 仅 owner 列表，保持兼容（不阻断 notify 接口）。
        self._collaborator_repo = collaborator_repo

    def list_bot_mappings(self, user_id: str) -> list[tuple[str, str, str]]:
        env = get_current_env()
        _total, bindings = self._binding_repo.list_bindings(
            entity_id=user_id,
            entity_type="staff",
            env=env,
            status="ACTIVE",
            page=1,
            page_size=100,
        )
        logger.info(
            f"[notify_bot_lister] user={user_id} env={env} "
            f"active bindings count={len(bindings)}"
        )
        result: list[tuple[str, str, str]] = []
        seen_bot_ids: set[str] = set()
        for b in bindings:
            props = b.device_props or {}
            sandbox_id = props.get("sandbox_id") or b.device_id
            if not sandbox_id:
                continue
            bot_id = str(props.get("bolt_id") or b.device_id)
            bot_name = self._resolve_bot_name(bot_id, user_id)
            result.append((bot_id, bot_name, sandbox_id))
            seen_bot_ids.add(bot_id)
        logger.info(
            f"[notify_bot_lister] user={user_id} owner mappings={len(result)}"
        )

        # 第二来源：当前用户作为协作者参与的 Bot。设备绑定归属 owner，
        # 故按 (bot_id, owner_id) 反查 owner 的 active binding 拿 sandbox。
        collaborator_mappings = self._list_collaborator_mappings(
            user_id, env, seen_bot_ids
        )
        result.extend(collaborator_mappings)
        logger.info(
            f"[notify_bot_lister] user={user_id} "
            f"collaborator mappings={len(collaborator_mappings)} "
            f"returning {len(result)} mappings"
        )
        return result

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _resolve_bot_name(self, bot_id: str, owner_id: str) -> str:
        """Resolve display name; fall back to bot_id on failure.

        镜像原始实现：bot 名字优先从 ``BotRepository`` 取，查不到/报错时
        回退到 ``bot_id``。
        """
        bot_name = bot_id
        try:
            bot = self._bot_repo.get_by_id_and_owner(bot_id, owner_id)
            if bot:
                bot_name = bot.get("bot_name") or bot_id
        except Exception as e:
            logger.warning(
                f"[notify_bot_lister] bot lookup failed bot={bot_id}: {e}"
            )
        return bot_name

    def _list_collaborator_mappings(
        self,
        user_id: str,
        env: str,
        seen_bot_ids: set[str],
    ) -> list[tuple[str, str, str]]:
        """当前用户以「协作者」身份参与的 Bot 的 (bot_id, bot_name, sandbox_id)。

        协作者记录里的 ``owner_id`` 是 Bot 拥有者工号——设备绑定挂在 owner
        名下，因此用 ``get_active_by_bot_and_owner(bot_id, owner_id)`` 取
        active binding 以保证 sandbox_id 与 owner 自查路径完全一致。
        """
        if self._collaborator_repo is None:
            return []

        mappings: list[tuple[str, str, str]] = []
        try:
            collaborators = self._collaborator_repo.list_by_user(user_id, env)
        except Exception as e:
            logger.warning(
                f"[notify_bot_lister] list collaborators failed "
                f"user={user_id}: {e}"
            )
            return mappings
        logger.info(
            f"[notify_bot_lister] user={user_id} collaborator records="
            f"{len(collaborators)}"
        )
        for rec in collaborators:
            bot_id = str(rec.bot_id or "")
            owner_id = str(rec.owner_id or "")
            if not bot_id or bot_id in seen_bot_ids:
                # 跳过空 bot_id 以及与 owner 自查重复的 Bot（去重）。
                continue
            try:
                binding = self._binding_repo.get_active_by_bot_and_owner(
                    bot_id=bot_id, owner_id=owner_id
                )
            except Exception as e:
                logger.warning(
                    f"[notify_bot_lister] collaborator binding lookup failed "
                    f"bot={bot_id} owner={owner_id}: {e}"
                )
                continue
            if not binding:
                # 协作者机器人没有 active 设备绑定 → 无可探活的 sandbox,
                # 与 owner 路径行为一致：不展示。
                continue
            props = binding.device_props or {}
            sandbox_id = props.get("sandbox_id") or binding.device_id
            if not sandbox_id:
                continue
            bot_name = self._resolve_bot_name(bot_id, owner_id)
            mappings.append((bot_id, bot_name, str(sandbox_id)))
            seen_bot_ids.add(bot_id)
        return mappings
