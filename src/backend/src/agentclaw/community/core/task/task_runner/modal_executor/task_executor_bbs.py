"""BBS-specific execution helpers extracted from :mod:`task_executor`."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

logger = logging.getLogger(__name__)


class TaskExecutorBbsMixin:
    """BBS relay and state-machine binding behaviour for ``TaskExecutor``."""

    async def _bbs_execute_as_manager_worker_group(
        self,
        *,
        task_id: str,
        node_id: str,
        winner_bot_id: str,
        owner_user_id: str | None,
        task_instruction: str,
        deadline_monotonic: float,
    ) -> dict:
        """BBS 旁路:single bot 任务改由 manager_worker 协作群执行(受 singlebot_2_group 开关控制)。

        与 ``_dispatch_single_bot_2_group`` 同构地建 manager_worker 群(single bot 作 manager,人类
        owner 作观察者 participant),但**不复用 poller 异步收敛** —— BBS 收敛必须走 ``on_bbs_report``
        (保留 bbs_owner 释放 / ``is_bbs_recovery`` 旁路,见 ``engine.on_bbs_report``),故这里**阻塞轮询**
        群 session 至终态后把收割的群产出交回 bbs_runner 拼同一份 ``_scoped_patch``。

        返回形状对齐 ``bot.send_and_wait_async`` (``{"result","session_id"}``),让 bbs_runner 现有
        ``_bbs_output``/``_bbs_session`` 提取代码原样复用。仅"未达终态超时"抛 ``asyncio.TimeoutError``
        (由 bbs_runner ``try/except`` 回退 ``send_and_wait_async``);群进入终态(completed/failed/aborted)
        一律返回(不抛、不重复执行)。
        """
        driver_bot = str(winner_bot_id or "").partition(":")[0]
        if not driver_bot:
            raise BotIdentityResolutionError(
                f"bbs manager_worker 群缺 driver bot:winner_bot_id={winner_bot_id!r}"
            )
        loop_task_id = f"{task_id}::{node_id}"
        gf = GroupFormation(
            bot_ids=[driver_bot],
            collab_mode="manager_worker",
            group_name=f"{task_id}-{node_id}",
            members_info=[{"bot_id": driver_bot, "role": "manager"}],
            extend_props={
                "owner_user_id": owner_user_id or "",
                "manager_bot_id": driver_bot,
                "loop_task_id": loop_task_id,
                "task_instruction": task_instruction,
            },
        )
        logger.info(
            "[task][bbs_mode] manager_worker 群建群前 task=%s node=%s driver=%s owner=%s",
            task_id, node_id, driver_bot, owner_user_id,
        )
        gid = await self.form_coop_group(gf)
        session_id = await self.get_group_session(gid)
        if not session_id:
            raise asyncio.TimeoutError(
                f"bbs manager_worker 群无可用 session task={task_id} group={gid}"
            )
        logger.info(
            "[task][bbs_mode] manager_worker 群建群成功 task=%s group=%s session=%s → 阻塞轮询至终态",
            task_id, gid, session_id,
        )

        return {"session_id": session_id, "success": True}

        """
        # 阻塞轮询:终态判据 + output 来源**对齐** TaskExecutorResultPoller._poll_terminal(session 模)
        # 与 BcsSessionTranslator.adapt(sess.output → 末条 assistant content)。BBS 要的是**原始产出**
        # (root 上 plan 据此自验收),非 translator 内部解析出的 acceptance data,故照搬 output 取数而
        # 不调 adapt(免得把 content 当 acceptance JSON 解析)。终态判据集合同 poller _TERMINAL_SM。
        _TERMINAL = {"completed", "failed", "aborted"}
        while time.monotonic() < deadline_monotonic:
            group = await self._bcs.get_group(gid)
            sess = (group or {}).get("session") or {}
            status = str(sess.get("status") or "").lower()
            if status in _TERMINAL:
                msgs = await self._bcs.get_session_messages(session_id)
                output = sess.get("output")
                if output is None:
                    for m in reversed(msgs or []):
                        if isinstance(m, dict) and m.get("role") == "assistant":
                            output = m.get("content")
                            break
                if status in ("failed", "aborted"):
                    output = sess.get("error_message") or f"session_{status}"
                    logger.warning(
                        "[task][bbs_mode] manager_worker 群非正常终态 task=%s group=%s status=%s",
                        task_id, gid, status,
                    )
                    return {"result": output, "session_id": session_id, "success": False}
                logger.info(
                    "[task][bbs_mode] manager_worker 群终态收割 task=%s group=%s status=%s",
                    task_id, gid, status,
                )
                return {"result": output, "session_id": session_id, "success": True}
            await asyncio.sleep(3.0)
        raise asyncio.TimeoutError(
            f"bbs manager_worker 群轮询超时未达终态 task={task_id} group={gid}"
        )
        """

    async def run_bbs(self, execution_graph) -> None:
        """升 BBS 可恢复态后主动 bid→select→claim→dispatch(委托 bbs_runner)。
        延迟导入 bbs_runner 避免顶层循环依赖;bbs_runner 自身 best-effort 不抛。"""
        from agentclaw.community.core.task.task_runner.modal_executor import bbs_modal_executor

        await bbs_modal_executor.notify(
            execution_graph=execution_graph,
            bcn=self._bcn,
            bot=self._bot,
            graph=self._graph,
            backend_url=self._api_base_url,
            skill_name=bbs_modal_executor._BBS_SKILL_NAME,
            on_bbs_report=self._on_bbs_report,
            group_executor=self._bbs_execute_as_manager_worker_group,
        )

    @staticmethod
    def _state_machine_bindings(gf: GroupFormation) -> dict[str, dict[str, Any]]:
        """返回 workflow 逻辑 binding → 产品 Bot IDs；绝不使用 Bot ID 充当 binding key。"""
        explicit = gf.extend_props.get("participant_bindings")
        bindings: dict[str, dict[str, Any]] = {}
        if explicit is not None:
            if not isinstance(explicit, dict):
                raise BotIdentityResolutionError(
                    "participant_bindings must be a mapping"
                )
            for binding, raw_spec in explicit.items():
                name = str(binding).strip()
                if not name:
                    raise BotIdentityResolutionError(
                        "participant binding name must not be empty"
                    )
                if isinstance(raw_spec, dict):
                    ids = raw_spec.get("bot_ids") or []
                    source = str(raw_spec.get("source") or "manual")
                else:
                    ids = raw_spec
                    source = "manual"
                if isinstance(ids, str):
                    ids = [ids]
                if not isinstance(ids, list) or not ids:
                    raise BotIdentityResolutionError(
                        f"participant binding must contain bot_ids: {name}"
                    )
                bindings[name] = {
                    "source": source,
                    "bot_ids": [str(bot_id) for bot_id in ids],
                }
            return bindings

        for member in gf.members_info or []:
            if not isinstance(member, dict):
                continue
            role = str(member.get("role") or "").strip()
            bot_id = str(member.get("bot_id") or "").strip()
            if not role or not bot_id:
                continue
            binding = bindings.setdefault(role, {"source": "manual", "bot_ids": []})
            binding["bot_ids"].append(bot_id)
        return bindings
