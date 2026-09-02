"""TaskExecutor:三模态派发(single_bot/coop_group/bbs)+ 旁路 poller 登记入口。

dispatch(async):上游 start_run caller loop 上 gather+Semaphore await 端口 IO,拿到 run_id 即返回
(不等待结果);bbs 仅记日志。form_coop_group(async):BCS 建群壳。poller 为独立 daemon sidecar(同 TaskHarness)。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.identity import compose_bot_identity
from agentclaw.community.core.task.domain.models import TaskNode, TaskNodePatch
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import (
    BcsCreateGroupRequest,
)
from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiAuthError,
    OpenApiBadRequestError,
)
from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    BcsGroupHandle,
    SingleBotHandle,
)

logger = logging.getLogger(__name__)
_DISPATCH_CONCURRENCY = 8
_BCS_PARTICIPANT_ROLES = {"driver", "consultant", "manager", "worker", "observer"}

# 人类观察者(不发言)拉人机制:任务 owner 以 observer 角色被追加进协作群,
# routing_policy.inject_observers 让终产投递给观察者(观察者不参与发言)。bot_uuid=human_<owner_user_id>。
_HUMAN_OBSERVER_ROUTING_POLICY: dict[str, Any] = {"default_bot_final_delivery": "inject_observers"}
# 走人类观察者拉人的协作模式(state_machine 群 participants 不得带 role,故不在此拉人)。
_HUMAN_OBSERVER_MODES = {"chat", "manager_worker"}


def _human_observer_participant(owner_user_id: str) -> dict[str, Any]:
    """人类观察者参与者:``bot_uuid=human_<owner_user_id>``,role=observer(不发言)。"""
    participant = {"bot_uuid": f"human_{owner_user_id}", "bot_name": owner_user_id, "role": "observer"}
    logger.debug("[task][task-executor] _human_observer_participant owner=%s → %s", owner_user_id, participant)
    return participant

# BCN 协作群事件回调投递路径:投到 task 模块的 `/api/v1/collaboration/tasks/callback/report`
# (主 router 的 report 回投路由),该路由 ``callback.report_result`` → ``on_report`` 驱动图态(收口 DONE)。
# 注:该路由按 ``TaskCallbackDataDTO``(loop_task_id + result{success,...})校验 body;BCS event_subscriptions
# 推的是 CloudEvent(event_type/scope/data,无 loop_task_id)。要让 CloudEvent 真能走通,需在 /callback/report
# 侧把 CloudEvent 适配成 TaskCallbackDataDTO(或让该路由兼容 CloudEvent),否则会 422。
_BCN_EVENT_CALLBACK_PATH = "/api/v1/collaboration/tasks/callback/report"


class TaskExecutor:
    def __init__(
        self,
        *,
        bot,
        bcs,
        formatter,
        context,
        sink,
        poller,
        identity_resolver=None,
        graph=None,
        api_base_url: str = "",
        bcn: BcnService | None = None,
        bot_token_provider=None,
        task_settings=None,
        on_bbs_report=None,
    ) -> None:
        """bot: OpenApiBotPort|None; bcs: BcsClientPort|None; formatter: PromptFormatter|None;
        context: TaskContextBuilder|None; sink: ResultSink|None; poller: TaskExecutorResultPoller|None。
        graph: TaskGraphService|None,动态派发后把 group_id/session_id/run_id 落节点 run_info.extend_props
        (dashboard 可见);None 时跳过(单测/无图路径)。R0 骨架允许 None。
        bbs_runner 通过注入的 BcnService.list_bots_by_task_modes(复用统一 provider 身份)查询任务模式候选。
        api_base_url: 任务后端 base url,传给 bbs_runner 拼发给胜出 bot 的任务消息。
        task_settings: TaskSettingsServiceProtocol|None,读取 single_bot_skill_report 开关决定
        single_bot 回收链路(默认 poller 拉消息;开启后走 skill HTTP 上报,与 poller 互斥不并存)。"""
        self._bot = bot
        self._bcs = bcs
        self._bcn = bcn
        self._formatter = formatter
        self._context = context
        self._sink = sink
        self._poller = poller
        self._identity_resolver = identity_resolver
        self._graph = graph
        self._api_base_url = api_base_url
        self._bot_token_provider = bot_token_provider
        self._task_settings = task_settings
        self._on_bbs_report = on_bbs_report  # 引擎 on_bbs_report 收口回调(供 run_bbs→notify 走引擎收敛)
        self._group_meta: dict[
            str, dict[str, Any]
        ] = {}  # group_id -> {collab_mode, gf, definition_ref, session_id}

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        sem = asyncio.Semaphore(_DISPATCH_CONCURRENCY)
        logger.info(
            "[task][task-executor] dispatch 入口 nodes=%s modes=%s",
            [n.node_id for n in toDoTaskList],
            [n.run_info.run_mode for n in toDoTaskList],
        )

        async def _one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode == "bbs":
                logger.info(
                    "[task][task_executor] bbs node dispatched (no-op): task=%s node=%s assignee=%s",
                    node.task_id,
                    node.node_id,
                    node.run_info.assignee,
                )
                return True
            if mode == "single_bot":
                logger.info(
                    "[task][task-executor] >>> 投递 single_bot task=%s node=%s bot=%s → send_message",
                    node.task_id,
                    node.node_id,
                    node.run_info.assignee,
                )
                return await self._dispatch_single_bot(node, sem)
            if mode == "coop_group":
                logger.info(
                    "[task][task-executor] >>> 投递 coop_group task=%s node=%s → form_coop_group(create_group)",
                    node.task_id,
                    node.node_id,
                )
                return await self._dispatch_coop_group(node, sem)
            logger.warning(
                "[task][task-executor] node=%s 未知 run_mode=%s → 不投递",
                node.node_id,
                mode,
            )
            return False

        return list(await asyncio.gather(*[_one(n) for n in toDoTaskList]))

    def _single_bot_skill_report_enabled(self) -> bool:
        """single_bot 回收链路开关(默认 False=poller 拉消息回收)。

        开启(True)时 single_bot 改走 skill HTTP 上报链路(predict：bot POST /callback/report),
        与 poller 互斥:本方法返回 True 时 ``_dispatch_single_bot`` **不注册 poller**,避免双链路并存
        导致双重收敛或 sla_timeout 噪声。开关未注入(non-prod/单测缺 task_settings)时回退 False(默认 poller)。
        """
        ts = self._task_settings
        if ts is None:
            return False
        try:
            return ts.is_enabled("single_bot_skill_report")
        except Exception as exc:  # noqa: BLE001 未知 setting_type / 读取失败 → fail-open 回退 poller
            logger.warning(
                "[task][task-executor] single_bot_skill_report 读取失败 → 回退 poller: %s",
                exc,
            )
            return False

    def _singlebot_2_group_enabled(self, task_id: str) -> bool:
        """singlebot_2_group 旁路开关(默认 True):single_bot 改建"二人 chat 群"(driver bot + 人类观察者,不发言)。
        从 ``graph.extend_props["execution_config"]`` 读;graph 不可用/缺键 → True(默认走旁路);显式 False → 老链路。"""
        if self._graph is None:
            logger.info("[task][task-executor] singlebot_2_group 开关:graph 未接 → 默认 True(走旁路) task=%s", task_id)
            return True
        try:
            snapshot = self._graph.query_task_dashboard(task_id)
        except Exception:  # noqa: BLE001 graph 不可用 → 默认走旁路
            logger.warning("[task][task-executor] singlebot_2_group 开关:graph 查询失败 → 默认 True task=%s", task_id)
            return True
        cfg = (getattr(snapshot, "extend_props", None) or {}).get("execution_config") or {}
        if not isinstance(cfg, dict):
            logger.info("[task][task-executor] singlebot_2_group 开关:execution_config 非 dict → 默认 True task=%s", task_id)
            return True
        val = cfg.get("singlebot_2_group", True)
        enabled = val if isinstance(val, bool) else str(val).lower() not in ("false", "0", "no", "none", "")
        logger.info(
            "[task][task-executor] singlebot_2_group 开关:execution_config.singlebot_2_group=%s → enabled=%s task=%s",
            val, enabled, task_id,
        )
        return enabled

    async def _dispatch_single_bot(
        self, node: TaskNode, sem: asyncio.Semaphore
    ) -> bool:
        """single_bot 派发。

        授权过滤(claim_on JOIN)已在派发策略层(``SearchBasedDispatchStrategy._apply_claim_join``)
        完成,执行器不再做表级授权 JOIN:直接按 ``node.run_info.assignee`` 投递。被 JOIN 丢掉的候选
        已由 dispatcher 写入 ``run_info.extend_props.unauthorized_bots``(dashboard 暴露)。"""
        assignee = node.run_info.assignee
        assignee_owner_id = node.run_info.extend_props.get("assignee_owner_id")
        openapi_bot_id = compose_bot_identity(assignee, assignee_owner_id)
        loop_task_id = f"{node.task_id}::{node.node_id}"
        skill_report = self._single_bot_skill_report_enabled()
        session_id: str | None = None
        async with sem:
            # P2 旁路:singlebot_2_group(默认 true)且 owner 在场且 bcs/identity_resolver/graph 已接
            # → 建"二人 chat 群"(driver=assignee bot + 人类观察者,不发言),按 coop_group 收敛;不发 send_message。
            _owner_present = bool(assignee_owner_id)
            _bcs_wired = self._bcs is not None and self._identity_resolver is not None
            _flag = self._singlebot_2_group_enabled(node.task_id)
            _bypass = _flag and _owner_present and _bcs_wired and self._graph is not None
            logger.info(
                "[task][task-executor] single_bot 派发 task=%s node=%s assignee=%s owner_present=%s bcs_wired=%s graph=%s flag=%s → bypass=%s",
                node.task_id, node.node_id, assignee, _owner_present, _bcs_wired,
                self._graph is not None, _flag, _bypass,
            )
            if _bypass:
                try:
                    return await self._dispatch_single_bot_2_group(
                        node, openapi_bot_id, assignee_owner_id, loop_task_id
                    )
                except Exception:  # noqa: BLE001 旁路建群失败 → 回退老链路(不阻断 single_bot 投递)
                    logger.exception(
                        "[task][task-executor] singlebot_2_group 旁路失败 → 回退老链路 task=%s node=%s",
                        node.task_id, node.node_id,
                    )
            logger.info(
                "[task][task-executor] single_bot 走老链路(send_message) task=%s node=%s bot=%s",
                node.task_id, node.node_id, openapi_bot_id,
            )
            try:
                ctx = dict(self._context.build(node.task_id, node.node_id) or {})
                ctx.update({
                    "task_id": node.task_id,
                    "node_id": node.node_id,
                    "execution_mode": "single_bot",
                    # single_bot 走哪条回收链路由开关决定(默认 poller):
                    #   False → bot 在终态消息内产出 {success,data,gaps} JSON,poller 拉消息回收(formatter 不下发 HTTP POST);
                    #   True  → bot 主动 HTTP POST /callback/report 上报,下不注册 poller(与上报互斥不并存)。
                    "single_bot_skill_report": skill_report,
                    "backend": self._api_base_url,
                })
                message = self._formatter.format_execute(ctx, node)
                sent = await self._bot.send_message(
                    bot_id=openapi_bot_id,
                    message=message,
                    metadata={"biz_task_id": node.task_id},
                )
                run_id = sent.run_id
                session_id = sent.session_id
            except (OpenApiAuthError, OpenApiBadRequestError) as exc:
                logger.warning(
                    "[task][task-executor] single_bot 派发失败(OpenAPI %s)task=%s node=%s bot=%s: %s "
                    "→ 留 PENDING 交 harness;grep [task][openapi_bot] 看具体哪步(http)失败",
                    type(exc).__name__,
                    node.task_id,
                    node.node_id,
                    assignee,
                    exc,
                )
                return False
            if skill_report:
                logger.info(
                    "[task][task-executor] single_bot skill-report 开关已开 task=%s node=%s bot=%s "
                    "→ 不注册 poller(走 skill HTTP 上报链路,与 poller 互斥)",
                    node.task_id, node.node_id, assignee,
                )
            else:
                self._poller.register(
                    SingleBotHandle(
                        loop_task_id=loop_task_id,
                        run_id=run_id,
                        bot_id=openapi_bot_id,
                        registered_at=time.monotonic(),
                        session_id=session_id,
                    )
                )
            self._persist_dispatch_ids(node, session_id=session_id, run_id=run_id)
            return True

    async def _dispatch_single_bot_2_group(
        self,
        node: TaskNode,
        openapi_bot_id: str,
        owner_user_id: str,
        loop_task_id: str,
    ) -> bool:
        """P2 旁路:single_bot → "二人 chat 群"(driver=assignee bot + 人类观察者,不发言),任务指令进群
        context,按 coop_group 收敛(BcsGroupHandle);并落库 run_mode single_bot→coop_group +
        extend_props.actual_run_mode=single_bot。复用 form_coop_group(自动加人类观察者 + routing_policy)。"""
        logger.info(
            "[task][task-executor] singlebot_2_group 旁路入口 task=%s node=%s driver_bot=%s owner=%s loop_task_id=%s",
            node.task_id, node.node_id, openapi_bot_id, owner_user_id, loop_task_id,
        )
        ctx = dict(self._context.build(node.task_id, node.node_id) or {})
        ctx.update({
            "task_id": node.task_id,
            "node_id": node.node_id,
            "execution_mode": "single_bot",
            "backend": self._api_base_url,
        })
        message = self._formatter.format_execute(ctx, node)
        gf = GroupFormation(
            bot_ids=[openapi_bot_id],
            collab_mode="chat",
            group_name=node.run_info.extend_props.get("group_name") or f"{node.task_id}-{node.node_id}",
            members_info=[{"bot_id": openapi_bot_id, "role": "driver"}],
            extend_props={
                "owner_user_id": owner_user_id,
                "loop_task_id": loop_task_id,
                "originator": f"human_{owner_user_id}",
                "task_instruction": message,
            },
        )
        logger.info(
            "[task][task-executor] singlebot_2_group 建群前 task=%s node=%s driver=%s collab=chat owner=%s",
            node.task_id, node.node_id, openapi_bot_id, owner_user_id,
        )
        gid = await self.form_coop_group(gf)
        logger.info(
            "[task][task-executor] singlebot_2_group 建群成功 task=%s node=%s group_id=%s",
            node.task_id, node.node_id, gid,
        )
        # 落库:run_mode single_bot→coop_group(收敛按协作群),extend_props 记 actual_run_mode=single_bot(原模式留痕)。
        if self._graph is not None:
            self._graph.update_task_node_info(
                TaskNodePatch(
                    task_id=node.task_id,
                    node_id=node.node_id,
                    run_mode="coop_group",
                    extend_props_patch={"actual_run_mode": "single_bot"},
                )
            )
            logger.info(
                "[task][task-executor] singlebot_2_group run_mode 落库 single_bot→coop_group + actual_run_mode=single_bot task=%s node=%s",
                node.task_id, node.node_id,
            )
        session_id = await self.get_group_session(gid)
        logger.info(
            "[task][task-executor] singlebot_2_group 取 session=%s task=%s node=%s group_id=%s",
            session_id, node.task_id, node.node_id, gid,
        )
        self._poller.register(
            BcsGroupHandle(
                loop_task_id=loop_task_id,
                group_id=gid,
                collab_mode="chat",
                registered_at=time.monotonic(),
                session_id=session_id,
                run_id=None,
            )
        )
        node.run_info.assignee = gid
        node.run_info.run_mode = "coop_group"
        self._persist_dispatch_ids(node, group_id=gid, session_id=session_id, run_id=None)
        logger.info(
            "[task][task-executor] singlebot_2_group 旁路完成 task=%s node=%s group_id=%s session=%s → BcsGroupHandle 收敛",
            node.task_id, node.node_id, gid, session_id,
        )
        return True

    async def _dispatch_coop_group(

        self, node: TaskNode, sem: asyncio.Semaphore
    ) -> bool:
        group_id = node.run_info.assignee
        meta = self._group_meta.get(group_id)
        collab_mode = (meta or {}).get("collab_mode", "chat")
        loop_task_id = f"{node.task_id}::{node.node_id}"
        async with sem:
            if collab_mode == "state_machine":
                return await self._dispatch_state_machine(
                    node, group_id, meta, loop_task_id
                )
            # chat / manager_worker:建群(create_group)已把任务指令作为 context 投入、且自带初始 session;
            # 复用该初始 session(get_group_session),不再 create_session 重复建群里的第二个 session。
            session_id = await self.get_group_session(group_id)
            self._poller.register(
                BcsGroupHandle(
                    loop_task_id=loop_task_id,
                    group_id=group_id,
                    collab_mode=collab_mode,
                    registered_at=time.monotonic(),
                    session_id=session_id,
                    run_id=None,
                )
            )
            self._persist_dispatch_ids(
                node, group_id=group_id, session_id=session_id, run_id=None
            )
            return True

    async def _dispatch_state_machine(self, node, group_id, meta, loop_task_id) -> bool:
        ctx = self._context.build(node.task_id, node.node_id)
        prompt = self._formatter.format_execute(ctx, node)
        definition_ref = (meta or {}).get("definition_ref")
        run_id = await self._bcs.start_state_machine_run(
            group_id,
            definition_yaml=None,
            definition_ref=definition_ref,
            session_id=None,
            input={"query": prompt},
        )
        self._poller.register(
            BcsGroupHandle(
                loop_task_id=loop_task_id,
                group_id=group_id,
                collab_mode="state_machine",
                registered_at=time.monotonic(),
                session_id=None,
                run_id=run_id,
            )
        )
        self._persist_dispatch_ids(
            node, group_id=group_id, session_id=None, run_id=run_id
        )
        return True

    def _persist_dispatch_ids(
        self,
        node: TaskNode,
        *,
        group_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """动态派发后把 group_id/session_id/run_id 落进节点 run_info.extend_props(dashboard 可见)。
        协作群(``coop_group``)写 group_id+session_id(chat/manager_worker)或 group_id+run_id(state_machine);
        单 bot(``single_bot``)无群,只写 session_id 与 run_id(group_id 留空不落键),与协作群链路对齐,
        便于 dashboard 观测及将来按 session_id 回调收敛。仅 extend_props fold,不翻态(节点已由 _drain 置 RUNNING)。"""
        if self._graph is None:
            return
        ep: dict[str, Any] = {}
        if group_id is not None:
            ep["group_id"] = group_id
        if session_id is not None:
            ep["session_id"] = session_id
        if run_id is not None:
            ep["run_id"] = run_id
        self._graph.update_task_node_info(
            TaskNodePatch(
                task_id=node.task_id, node_id=node.node_id, extend_props_patch=ep
            )
        )

    def _resolve_owner_user_id(self, gf: GroupFormation) -> str | None:
        """解析任务 owner_user_id:优先 ``gf.extend_props["owner_user_id"]``(P2 直接注入);
        否则按 ``loop_task_id``/``task_id`` 反查 ``graph.extend_props["owner_user_id"]``(覆盖 engine._drain
        协作派发路径[GF 带 loop_task_id] 与 _run_yaml/start_coop_group 路径[GF 带 task_id],不改 dispatch/planning)。
        无则 None → 不拉人类观察者。"""
        explicit = gf.extend_props.get("owner_user_id")
        if explicit:
            logger.info("[task][task_executor] resolve_owner_user_id 命中 gf.extend_props[owner_user_id] owner=%s", explicit)
            return str(explicit)
        loop_task_id = gf.extend_props.get("loop_task_id") or ""
        # _drain 协作派发路径 GF 带 loop_task_id(task::node);_run_yaml/start_coop_group 路径 GF 带 task_id(无 loop_task_id)。
        task_id = (loop_task_id.split("::", 1)[0] if loop_task_id else "") or (gf.extend_props.get("task_id") or "")
        if task_id and self._graph is not None:
            try:
                snapshot = self._graph.query_task_dashboard(task_id)
            except Exception:  # noqa: BLE001 graph 不可用/查询失败 → 不阻断建群,仅不拉人
                logger.warning("[task][task_executor] resolve owner_user_id 查询失败 task=%s", task_id)
                return None
            owner = (getattr(snapshot, "extend_props", None) or {}).get("owner_user_id")
            logger.info("[task][task_executor] resolve_owner_user_id 反查 graph task=%s owner=%s", task_id, owner)
            return str(owner) if owner else None
        logger.info(
            "[task][task_executor] resolve_owner_user_id 无来源(owner_user_id/loop_task_id/task_id 均无, graph=%s)→ 不拉人类观察者",
            self._graph is not None,
        )
        return None

    async def form_coop_group(self, gf: GroupFormation) -> str:
        bot_ids = list(dict.fromkeys(gf.bot_ids))
        if not bot_ids:
            raise BotIdentityResolutionError("cannot form a group without bots")
        if self._identity_resolver is None:
            raise BotIdentityResolutionError(
                "BCS Bot identity resolver is not configured"
            )
        mode = gf.collab_mode
        member_roles = {
            str(member.get("bot_id")): str(member.get("role"))
            for member in (gf.members_info or [])
            if isinstance(member, dict) and member.get("bot_id") and member.get("role")
        }
        raw_bindings = (
            self._state_machine_bindings(gf) if mode == "state_machine" else {}
        )
        # A state-machine binding names the actual runtime Bot(s). BCS requires
        # every binding target to also be present in participants, so merge
        # binding targets into the participant roster before resolving UUIDs.
        # Keep the original order and deduplicate exact product/composite IDs.
        for spec in raw_bindings.values():
            for binding_bot_id in spec["bot_ids"]:
                if binding_bot_id not in bot_ids:
                    bot_ids.append(binding_bot_id)
        manager_bot_id = (
            gf.extend_props.get("manager_bot_id") if mode == "manager_worker" else None
        )
        originator_bot_id = gf.extend_props.get("originator_bot_id")
        referenced_ids = list(bot_ids)
        if manager_bot_id:
            referenced_ids.append(str(manager_bot_id))
        if originator_bot_id:
            referenced_ids.append(str(originator_bot_id))
        for spec in raw_bindings.values():
            referenced_ids.extend(spec["bot_ids"])
        referenced_ids = list(dict.fromkeys(referenced_ids))
        # owner_bot_id 切分补全丢 ``:owner`` 后 ``bot_ids[0]`` 为纯 bot_id,而 participants /
        # participant_bindings 透传全 ``bot:owner`` 串 —— 校验须按纯 bot_id(``:owner`` 剥离)归一化比对,
        # 否则 owner 同时被 ``bot_ids``(纯)与 ``participant_bindings``(全串)引用时被假性判"不在
        # GroupFormation.bot_ids"(regression:预发 e2e owner=20260825_bohtfhe6:35983 兼 binding writer)。
        _allowed = {bot_id.partition(":")[0] for bot_id in bot_ids}
        unknown = [bot_id for bot_id in referenced_ids if bot_id.partition(":")[0] not in _allowed]
        if unknown:
            raise BotIdentityResolutionError(
                f"group bindings reference bots outside GroupFormation.bot_ids: {unknown}"
            )
        logger.info(
            "[task][task_executor] form_coop_group start collab=%s bot_ids=%s referenced_ids=%s manager_bot_id=%s originator_bot_id=%s",
            mode,
            bot_ids,
            referenced_ids,
            manager_bot_id,
            originator_bot_id,
        )
        try:
            resolved = self._identity_resolver.resolve_many(referenced_ids)
        except Exception:
            logger.exception(
                "[task][task_executor] form_coop_group identity resolution failed collab=%s bot_ids=%s referenced_ids=%s",
                mode,
                bot_ids,
                referenced_ids,
            )
            raise
        logger.info(
            "[task][task_executor] form_coop_group identities resolved collab=%s resolved=%s",
            mode,
            resolved,
        )

        def bcs_uuid(product_bot_id: str) -> str:
            try:
                return resolved[product_bot_id]
            except KeyError as exc:
                raise BotIdentityResolutionError(
                    f"BCS identity resolver omitted bot_id: {product_bot_id}"
                ) from exc

        participants = []
        for index, product_bot_id in enumerate(bot_ids):
            participant: dict[str, Any] = {"bot_uuid": bcs_uuid(product_bot_id)}
            requested_role = member_roles.get(product_bot_id, "")
            if mode == "state_machine":
                # state_machine 群的 participants.role 由 BCS 自行推断(按 bot_uuid vs driver_bot),
                # 请求不得带 role —— 否则 BCS 400 "participants.role is inferred by BCS and must
                # not be provided"(groups.rs:group_create_participants)。故只放 bot_uuid;
                # 逻辑角色(writer/editor 等)经 participant_bindings 绑定。
                pass
            elif requested_role in _BCS_PARTICIPANT_ROLES:
                participant["role"] = requested_role
            else:
                participant["role"] = "driver" if index == 0 else "consultant"
            participants.append(participant)
        req_kwargs: dict[str, Any] = {
            "driver_bot": bcs_uuid(bot_ids[0]),
            "participants": participants,
        }

        _api_base = gf.extend_props.get("api_base_url")
        _corp_cb = ""
        if self._bcs is not None:
            _cb_fn = getattr(self._bcs, "task_callback_url", None)
            if callable(_cb_fn):
                _corp_cb = (_cb_fn() or "").strip()
        _sink_base = _corp_cb or _api_base or self._api_base_url
        logger.info("[task][task_executor] form_coop_group sink_base_url=%s, api_base=%s", _sink_base, _api_base)

        if mode == "manager_worker":
            mgr = str(manager_bot_id or bot_ids[0])
            req_kwargs["group_strategy"] = "manager_worker"
            req_kwargs["driver_bot"] = bcs_uuid(mgr)
            req_kwargs["participants"] = [
                {"bot_uuid": bcs_uuid(mgr), "role": "manager"}
            ] + [
                {"bot_uuid": bcs_uuid(b), "role": "worker"} for b in bot_ids if b != mgr
            ]
        elif mode == "state_machine":
            req_kwargs["group_strategy"] = "state_machine"
            # GroupFormation.extend_props["definition_yaml"] → BCS collaboration_definition_yaml
            def_yaml = gf.extend_props.get("definition_yaml") or gf.extend_props.get(
                "collaboration_definition_yaml"
            )
            if def_yaml is not None:
                req_kwargs["collaboration_definition_yaml"] = def_yaml
            if raw_bindings:
                req_kwargs["participant_bindings"] = {
                    binding: {
                        "source": spec["source"],
                        "bot_ids": [bcs_uuid(bot_id) for bot_id in spec["bot_ids"]],
                    }
                    for binding, spec in raw_bindings.items()
                }
            # 默认让 BCS 建群即自动开跑初始状态机(YAML 路径 + 动态派发均如此);
            # 调用方可经 extend_props["start_initial_run"]=False 显式关闭、改为手动 start_state_machine_run。
            req_kwargs["start_initial_run"] = bool(
                gf.extend_props.get("start_initial_run", True)
            )
            # state_machine 群需 opening_message(task-loop panel),taskId = 任务ID
            _task_id = gf.extend_props.get("task_id")
            panel_component_name = gf.extend_props.get("panel_component_name")
            if _task_id and panel_component_name:
                # BCS 契约:opening_message.params 必须是 JSON object,不能字符串化
                # (见 ocb-public/src/bcs/docs/custom-collaboration-opening-message-integration-guide.md §4:
                # params = 传给业务组件的 JSON object)。字符串化会被真实 BCS 判
                # "data did not match any variant of untagged enum OpeningMessage" 422。
                req_kwargs["opening_message"] = {
                    "type": "panel",
                    "component": str(panel_component_name),
                    "params": {
                        "groupId": "{{bcs.group_id}}",
                        "sessionId": "{{bcs.session_id}}",
                        "runId": "{{bcs.run_id}}",
                        "groupName": "{{bcs.group_name}}",
                        "sessionName": "{{bcs.session_name}}",
                        "businessScene": "release_review",
                        "taskId": _task_id,
                        "apiBaseUrl": gf.extend_props.get("api_base_url") or "",
                    },
                }
        _raw_originator = gf.extend_props.get("originator")
        logger.info("[task][task_executor] raw_originator = %s", _raw_originator)
        if _raw_originator:
            req_kwargs["originator"] = str(_raw_originator)
        elif originator_bot_id:
            req_kwargs["originator"] = bcs_uuid(str(originator_bot_id))
        service_spec = gf.extend_props.get("service_spec")
        if service_spec:
            req_kwargs["service_spec"] = service_spec
        if self._bot_token_provider is not None:
            req_kwargs["caller_bot_token"] = self._bot_token_provider.get_token(
                req_kwargs.get("driver_bot") or ""
            )
        # BCN 事件回调订阅仅对 manager_worker 生效。state_machine/chat 使用 poller
        # 兜底收敛，避免 BCS require_human 拒绝 Bot/HMAC-only 建群请求。
        if mode == "manager_worker" and _sink_base:
            _sink_base = str(_sink_base).rstrip("/")
            req_kwargs["event_subscriptions"] = [
                {
                    "name": "avernet-manager-worker",
                    "event_filters": [
                        "session.created",
                        "task.assigned",
                        "task.completed",
                        "session.completed",
                    ],
                    "payload": {"mode": "full"},
                    "sink": {
                        "type": "webhook",
                        "url": _sink_base + _BCN_EVENT_CALLBACK_PATH,
                        "request_timeout_ms": 10000,
                    },
                }
            ]

        if mode == "state_machine" and _sink_base:
            _sink_base = str(_sink_base).rstrip("/")
            req_kwargs["event_subscriptions"] = [
                {
                    "name": "avernet-state_machine",
                    "event_filters": [
                        'state_machine.run.created',
                        'state_machine.run.started',
                        'state_machine.node.started',
                        'state_machine.node.completed',
                        'state_machine.run.completed'
                    ],
                    "payload": {"mode": "full"},
                    "sink": {
                        "type": "webhook",
                        "url": _sink_base + _BCN_EVENT_CALLBACK_PATH,
                        "request_timeout_ms": 10000,
                    },
                }
            ]
        # 任务描述(目标)→ BCS 创建群的 context 字段。BCS ``resolve_session_topic`` 把 group.context
        # 兜底注入 <GroupContext> 的 `目标` 行(session input 为空时,如建群 BotJoined)。
        _task_context = gf.extend_props.get("task_context")
        # 任务验收 push 链路:协作群叶子派发期注入 loop_task_id,此处写入群 context,
        # 供 driver/owner bot 按 acceptance 段4 自验收后 push 回投 /callback/report
        # (loop_task_id 定位执行节点;backend 取本 TaskExecutor 的 api_base_url,不写死)。
        _loop_task_id = gf.extend_props.get("loop_task_id")
        if _loop_task_id and self._api_base_url:
            _task_context = (
                (_task_context or "")
                + f"\n[task-loop] loop_task_id={_loop_task_id}; backend={self._api_base_url}"
            )
        _acceptances = [
            {"id": a.get("id"), "description": a.get("description")}
            for a in (gf.extend_props.get("acceptances") or [])
            if isinstance(a, dict) and a.get("id")
        ]
        # All group members receive the context, but exactly one Bot owns the
        # terminal acceptance callback. Resolve it from the group semantics:
        # manager for manager-worker, BCS driver for state-machine, and the
        # originator/first driver for free-chat groups.
        if mode == "manager_worker":
            _reporter_bot_id = str(
                gf.extend_props.get("manager_bot_id")
                or (gf.bot_ids[0] if gf.bot_ids else "")
            )
            _reporter_role = "master/manager"
        elif mode == "state_machine":
            _reporter_bot_id = str(gf.bot_ids[0] if gf.bot_ids else "")
            _reporter_role = "master/BCS driver"
        else:
            _reporter_bot_id = str(
                gf.extend_props.get("originator_bot_id")
                or (gf.bot_ids[0] if gf.bot_ids else "")
            )
            _reporter_role = "拉群 Bot/driver"
        _task_objective = str(
            gf.extend_props.get("task_objective") or _task_context or ""
        )
        _task_instruction = str(gf.extend_props.get("task_instruction") or "")
        if _task_objective or _task_instruction or _loop_task_id:
            if str(_task_instruction).lstrip().startswith("# 接自"):
                # 接力协作群:群成员收到的 context 以"# 接自/## 群组成/## 上游产出正文/## 本群任务"
                # 接力交接正文先导(与 single_bot format_execute 直发交接同口径),再附 driver 回投定位
                # 协议作脚注;不再重复注入验收叙事/目标/字段要求/禁联网——回收与验收由各 bot 的
                # skill/rule + 框架 80s 兜底承托,与 single_bot 接力保持一致。
                _rfooter = [
                    "---",
                    "[协作群回投协议 — 仅 driver/reporter bot 上报回投,其它成员只提供产出,不得重复回调]",
                    f"reporter_bot_id={_reporter_bot_id}; reporter_role={_reporter_role}",
                    f"目标:{_task_objective}",
                    f"验收标准:{json.dumps(_acceptances, ensure_ascii=False)}",
                ]
                if _task_context:
                    _rfooter.append(f"任务上下文:{_task_context}")
                if _loop_task_id:
                    try:
                        _task_id, _node_id = _loop_task_id.split("::", 1)
                    except ValueError:
                        _task_id, _node_id = "<task_id>", "<node_id>"
                    _rfooter.append(
                        "回投请求体只能包含以下节点级字段;callback 内部会根据 task_id/node_id 组装 loop_task_id 等关联字段:"
                        + json.dumps({
                            "task_id": _task_id,
                            "node_id": _node_id,
                            "status": "DONE",
                            "output": "完整协作群执行输出",
                            "acceptance_result": {},
                            "extend_props": {},
                        }, ensure_ascii=False)
                        + "\n验收未通过时将 status 改为 DONE,并在 acceptance_result.gaps 填写具体差距;只有执行失败才使用 FAILED。"
                    )
                req_kwargs["context"] = f"{_task_instruction.rstrip()}\n" + "\n".join(_rfooter)
            else:
                # 非接力协作群:保留原 [task-execute] reporter/目标/指令/验收/任务上下文/回投体验收信封。
                req_kwargs["context"] = (
                    "[task-execute]\n"
                    "execution_mode=coop_group\n"
                    f"reporter_bot_id={_reporter_bot_id}\n"
                    f"reporter_role={_reporter_role}\n"
                    "只有 reporter_bot_id 对应的 Bot（本群唯一 master/driver）可以调用 "
                    "task-loop 的任务验收(acceptance)逻辑。所有 worker 完成或明确失败后，"
                    "reporter 必须立即逐条检查当前节点 goal.acceptances，汇总完整执行输出，"
                    "生成 DONE/FAILED，并真正 POST 回投；不得只在群里回复完成；其它 Bot 只提供产出，不得重复回调。\n"
                    "验收步骤不可跳过：执行→逐条校验→生成结论→HTTP上报→确认HTTP 200。\n"
                    "只有在上述完成条件满足后才触发 task-acceptance；建群初始上下文不触发验收。\n"
                    f"目标:{_task_objective}\n"
                    f"指令:{_task_instruction}\n"
                    f"验收标准:{json.dumps(_acceptances, ensure_ascii=False)}\n"
                    f"任务上下文:{_task_context or ''}"
                )
                if _loop_task_id:
                    try:
                        _task_id, _node_id = _loop_task_id.split("::", 1)
                    except ValueError:
                        _task_id, _node_id = "<task_id>", "<node_id>"
                    req_kwargs["context"] += (
                        "\n回投请求体只能包含以下节点级字段；callback 内部会根据 task_id/node_id 组装 loop_task_id 等关联字段："
                        + json.dumps({
                            "task_id": _task_id,
                            "node_id": _node_id,
                            "status": "DONE",
                            "output": "完整协作群执行输出",
                            "acceptance_result": {},
                            "extend_props": {},
                        }, ensure_ascii=False)
                        + "\n验收未通过时将 status 改为 DONE，并在 acceptance_result.gaps 填写具体差距;只有执行失败才使用 FAILED。"
                    )
        # 人类观察者(P1):任务 owner 以 observer 角色被拉入协作群(chat/manager_worker),不发言。
        # routing_policy.inject_observers 默认生效,终产投递给观察者;state_machine 群 participants 不得带 role,故跳过。
        if mode in _HUMAN_OBSERVER_MODES:
            _owner_user_id = self._resolve_owner_user_id(gf)
            if _owner_user_id:
                req_kwargs["participants"] = list(req_kwargs.get("participants") or []) + [
                    _human_observer_participant(_owner_user_id)
                ]
                req_kwargs["routing_policy"] = dict(_HUMAN_OBSERVER_ROUTING_POLICY)
                req_kwargs.setdefault("label", gf.group_name or "")
                # 拉人接口示例:originator=human_<owner>(setdefault——已有显式 bot originator 时不覆盖)。
                req_kwargs.setdefault("originator", f"human_{_owner_user_id}")
                logger.info(
                    "[task][task_executor] form_coop_group 追加人类观察者(不发言) mode=%s owner=%s → routing_policy=inject_observers originator=human_%s",
                    mode, _owner_user_id, _owner_user_id,
                )
            else:
                logger.info("[task][task_executor] form_coop_group 无 owner_user_id → 不拉人类观察者 mode=%s", mode)

        req = BcsCreateGroupRequest(**req_kwargs)
        logger.info(
            "[task][task_executor] form_coop_group create_group request collab=%s driver_bot=%s participants=%s group_strategy=%s has_definition=%s has_bindings=%s",
            mode,
            req.driver_bot,
            req.participants,
            req.group_strategy,
            bool(req.collaboration_definition_yaml),
            bool(req.participant_bindings),
        )
        try:
            logger.info("[task][task_executor] form_coop_group create_group request=%s", req)
            res = await self._bcs.create_group(req)
        except Exception:
            logger.exception(
                "[task][task_executor] form_coop_group create_group failed collab=%s driver_bot=%s participants=%s group_strategy=%s",
                mode,
                req.driver_bot,
                req.participants,
                req.group_strategy,
            )
            raise
        logger.info(
            "[task][task_executor] form_coop_group create_group succeeded collab=%s group_id=%s session_id=%s run_id=%s",
            mode,
            res.group_id,
            res.session_id,
            res.run_id,
        )
        self._group_meta[res.group_id] = {
            "collab_mode": mode,
            "gf": gf,
            "definition_ref": res.definition_ref,
            "session_id": res.session_id,
        }
        return res.group_id

    async def trigger_workflow(
        self, *, bot_id: str, message: str, metadata: dict[str, Any] | None = None
    ) -> BotSendResult:
        """Single-bot workflow trigger: send + register a SingleBotHandle; return BotSendResult."""
        sent = await self._bot.send_message(
            bot_id=bot_id, message=message, metadata=metadata or {}
        )
        biz_task_id = (metadata or {}).get("biz_task_id", "")
        self._poller.register(
            SingleBotHandle(
                loop_task_id=f"{biz_task_id}::{biz_task_id}",  # root node_id == task_id
                run_id=sent.run_id,
                bot_id=bot_id,
                registered_at=time.monotonic(),
                session_id=sent.session_id,
            )
        )
        return sent

    async def get_group_session(self, group_id: str) -> str | None:
        """取群的最近一个 session:建群响应若已带 session_id 则用之;否则经
        ``GET /groups/{group_id}`` 响应的 ``latest_running_session_id`` 取最近 running session。
        不再 ``create_session`` 新建(避免给群重复建 session)。"""
        meta = self._group_meta.get(group_id)
        sid = (meta or {}).get("session_id")
        if sid is None and self._bcs is not None:
            detail = await self._bcs.get_group(group_id)
            sid = (detail or {}).get("latest_running_session_id")
        return sid

    async def run_bbs(self, execution_graph) -> None:
        """升 BBS 可恢复态后主动 bid→select→claim→dispatch(委托 bbs_runner)。
        延迟导入 bbs_runner 避免顶层循环依赖;bbs_runner 自身 best-effort 不抛。"""
        from agentclaw.community.core.task.task_runner.integration import bbs_runner

        await bbs_runner.notify(
            execution_graph=execution_graph,
            bcn=self._bcn,
            bot=self._bot,
            graph=self._graph,
            backend_url=self._api_base_url,
            skill_name=bbs_runner._BBS_SKILL_NAME,
            on_bbs_report=self._on_bbs_report,
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

    async def aclose(self) -> None:
        if self._poller is not None:
            self._poller.stop()
