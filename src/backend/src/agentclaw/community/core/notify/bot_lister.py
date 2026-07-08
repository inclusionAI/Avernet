"""Prod-mode NotifyBotLister.

走 ``DeviceBindingRepository``——这是唯一能稳定拿到 sandbox_id 的路径，
与 ``ProdHealthProbe.bots_health`` / ``probe_engine_prod`` 完全一致:

  - ``entity_type="staff"`` —— 真实表里 owner 是 staff,不是 "USER"
  - ``env=get_current_env()`` —— 防跨环境串数据
  - ``status="ACTIVE"`` —— 只取活绑定
  - ``sandbox_id`` 回退到 ``binding.device_id`` —— 老版本绑定没写 device_props

bot 名字仍然从 ``BotRepository`` 解析 (device_props.bolt_id → bot_id)。
"""
from __future__ import annotations

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
    ) -> None:
        self._binding_repo = binding_repo
        self._bot_repo = bot_repo

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
        for b in bindings:
            props = b.device_props or {}
            sandbox_id = props.get("sandbox_id") or b.device_id
            if not sandbox_id:
                continue
            bot_id = str(props.get("bolt_id") or b.device_id)
            bot_name = bot_id
            try:
                bot = self._bot_repo.get_by_id_and_owner(bot_id, user_id)
                if bot:
                    bot_name = bot.get("bot_name") or bot_id
            except Exception as e:
                logger.warning(
                    f"[notify_bot_lister] bot lookup failed bot={bot_id}: {e}"
                )
            result.append((bot_id, bot_name, sandbox_id))
        logger.info(
            f"[notify_bot_lister] user={user_id} returning {len(result)} mappings"
        )
        return result
