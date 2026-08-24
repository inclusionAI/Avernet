"""TaskExecutor:三模态派发(single_bot/coop_group/bbs)+ 旁路 poller 登记入口。

dispatch(async):上游 start_run caller loop 上 gather+Semaphore await 端口 IO,拿到 run_id 即返回
(不等待结果);bbs 仅记日志。form_coop_group(async):BCS 建群壳。poller 为独立 daemon sidecar(同 TaskHarness)。
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from agentclaw.community.core.task.domain.models import TaskNode, TaskNodePatch
from agentclaw.community.core.bot_management.services.bcn_service import BcnService
from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation

from agentclaw.community.core.task.task_runner.integration.bcs_http_adapter import BcsCreateGroupRequest
from agentclaw.community.core.task.task_runner.integration.open_api_bot_adapter import (
    OpenApiAuthError, OpenApiBadRequestError,
)
from agentclaw.community.core.task.task_runner.integration.ports import BotSendResult
from agentclaw.community.core.task.task_runner.integration.task_executor_result_poller import (
    BcsGroupHandle, SingleBotHandle,
)

logger = logging.getLogger(__name__)
_DISPATCH_CONCURRENCY = 8
_BCS_PARTICIPANT_ROLES = {"driver", "consultant", "manager", "worker", "observer"}

# BCN 协作群事件回调投递路径:投到 task 模块的 `/api/v1/collaboration/tasks/callback/report`
# (主 router 的 report 回投路由),该路由 ``callback.report_result`` → ``on_report`` 驱动图态(收口 DONE)。
# 注:该路由按 ``TaskCallbackDataDTO``(loop_task_id + result{success,...})校验 body;BCS event_subscriptions
# 推的是 CloudEvent(event_type/scope/data,无 loop_task_id)。要让 CloudEvent 真能走通,需在 /callback/report
# 侧把 CloudEvent 适配成 TaskCallbackDataDTO(或让该路由兼容 CloudEvent),否则会 422。
_BCN_EVENT_CALLBACK_PATH = "/api/v1/collaboration/tasks/callback/report"


class TaskExecutor:
    def __init__(self, *, bot, bcs, formatter, context, sink, poller,
                 identity_resolver=None, graph=None,
                 api_base_url: str = "", bcn: BcnService | None = None) -> None:
        """bot: OpenApiBotPort|None; bcs: BcsClientPort|None; formatter: PromptFormatter|None;
        context: TaskContextBuilder|None; sink: ResultSink|None; poller: TaskExecutorResultPoller|None。
        graph: TaskGraphService|None,动态派发后把 group_id/session_id/run_id 落节点 run_info.extend_props
        (dashboard 可见);None 时跳过(单测/无图路径)。R0 骨架允许 None。
        bbs_runner 通过注入的 BcnService.list_bots_by_task_modes(复用统一 provider 身份)查询任务模式候选。
        api_base_url: 任务后端 base url,传给 bbs_runner 拼发给胜出 bot 的任务消息。"""
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
        self._group_meta: dict[str, dict[str, Any]] = {}  # group_id -> {collab_mode, gf, definition_ref, session_id}

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        sem = asyncio.Semaphore(_DISPATCH_CONCURRENCY)

        async def _one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode == "bbs":
                logger.info("[task_executor] bbs node dispatched (no-op): task=%s node=%s assignee=%s",
                            node.task_id, node.node_id, node.run_info.assignee)
                return True
            if mode == "single_bot":
                return await self._dispatch_single_bot(node, sem)
            if mode == "coop_group":
                return await self._dispatch_coop_group(node, sem)
            return False

        return list(await asyncio.gather(*[_one(n) for n in toDoTaskList]))

    async def _dispatch_single_bot(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        bot_id = node.run_info.assignee
        loop_task_id = f"{node.task_id}::{node.node_id}"
        session_id: str | None = None
        async with sem:
            try:
                await self._bot.ensure_grant(bot_id)
                ctx = self._context.build(node.task_id, node.node_id)
                message = self._formatter.format_execute(ctx, node)
                sent = await self._bot.send_message(
                    bot_id=bot_id, message=message,
                    metadata={"biz_task_id": node.task_id},
                )
                run_id = sent.run_id
                session_id = sent.session_id
            except (OpenApiAuthError, OpenApiBadRequestError):
                return False
            self._poller.register(SingleBotHandle(
                loop_task_id=loop_task_id, run_id=run_id, bot_id=bot_id,
                registered_at=time.monotonic(), session_id=session_id,
            ))
            self._persist_dispatch_ids(node, session_id=session_id, run_id=run_id)
            return True

    async def _dispatch_coop_group(self, node: TaskNode, sem: asyncio.Semaphore) -> bool:
        group_id = node.run_info.assignee
        meta = self._group_meta.get(group_id)
        collab_mode = (meta or {}).get("collab_mode", "chat")
        loop_task_id = f"{node.task_id}::{node.node_id}"
        async with sem:
            if collab_mode == "state_machine":
                return await self._dispatch_state_machine(node, group_id, meta, loop_task_id)
            # chat / manager_worker:建群(create_group)已把任务指令作为 context 投入、且自带初始 session;
            # 复用该初始 session(get_group_session),不再 create_session 重复建群里的第二个 session。
            session_id = await self.get_group_session(group_id)
            self._poller.register(BcsGroupHandle(
                loop_task_id=loop_task_id, group_id=group_id, collab_mode=collab_mode,
                registered_at=time.monotonic(), session_id=session_id, run_id=None,
            ))
            self._persist_dispatch_ids(node, group_id=group_id, session_id=session_id, run_id=None)
            return True

    async def _dispatch_state_machine(self, node, group_id, meta, loop_task_id) -> bool:
        ctx = self._context.build(node.task_id, node.node_id)
        prompt = self._formatter.format_execute(ctx, node)
        definition_ref = (meta or {}).get("definition_ref")
        run_id = await self._bcs.start_state_machine_run(
            group_id, definition_yaml=None, definition_ref=definition_ref,
            session_id=None, input={"query": prompt},
        )
        self._poller.register(BcsGroupHandle(
            loop_task_id=loop_task_id, group_id=group_id, collab_mode="state_machine",
            registered_at=time.monotonic(), session_id=None, run_id=run_id,
        ))
        self._persist_dispatch_ids(node, group_id=group_id, session_id=None, run_id=run_id)
        return True

    def _persist_dispatch_ids(self, node: TaskNode, *, group_id: str | None = None,
                              session_id: str | None = None, run_id: str | None = None) -> None:
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
        self._graph.update_task_node_info(TaskNodePatch(
            task_id=node.task_id, node_id=node.node_id, extend_props_patch=ep))

    async def form_coop_group(self, gf: GroupFormation) -> str:
        bot_ids = list(dict.fromkeys(gf.bot_ids))
        if not bot_ids:
            raise BotIdentityResolutionError("cannot form a group without bots")
        if self._identity_resolver is None:
            raise BotIdentityResolutionError("BCS Bot identity resolver is not configured")
        mode = gf.collab_mode
        member_roles = {
            str(member.get("bot_id")): str(member.get("role"))
            for member in (gf.members_info or [])
            if isinstance(member, dict) and member.get("bot_id") and member.get("role")
        }
        raw_bindings = self._state_machine_bindings(gf) if mode == "state_machine" else {}
        manager_bot_id = gf.extend_props.get("manager_bot_id") if mode == "manager_worker" else None
        originator_bot_id = gf.extend_props.get("originator_bot_id")
        referenced_ids = list(bot_ids)
        if manager_bot_id:
            referenced_ids.append(str(manager_bot_id))
        if originator_bot_id:
            referenced_ids.append(str(originator_bot_id))
        for spec in raw_bindings.values():
            referenced_ids.extend(spec["bot_ids"])
        referenced_ids = list(dict.fromkeys(referenced_ids))
        if any(bot_id not in bot_ids for bot_id in referenced_ids):
            unknown = [bot_id for bot_id in referenced_ids if bot_id not in bot_ids]
            raise BotIdentityResolutionError(
                f"group bindings reference bots outside GroupFormation.bot_ids: {unknown}"
            )
        logger.info(
            "[task_executor] form_coop_group start collab=%s bot_ids=%s referenced_ids=%s manager_bot_id=%s originator_bot_id=%s",
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
                "[task_executor] form_coop_group identity resolution failed collab=%s bot_ids=%s referenced_ids=%s",
                mode, bot_ids, referenced_ids,
            )
            raise
        logger.info(
            "[task_executor] form_coop_group identities resolved collab=%s resolved=%s",
            mode, resolved,
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
        if mode == "manager_worker":
            mgr = str(manager_bot_id or bot_ids[0])
            req_kwargs["group_strategy"] = "manager_worker"
            req_kwargs["driver_bot"] = bcs_uuid(mgr)
            req_kwargs["participants"] = [
                {"bot_uuid": bcs_uuid(mgr), "role": "manager"}] + [
                {"bot_uuid": bcs_uuid(b), "role": "worker"} for b in bot_ids if b != mgr]
        elif mode == "state_machine":
            req_kwargs["group_strategy"] = "state_machine"
            # GroupFormation.extend_props["definition_yaml"] → BCS collaboration_definition_yaml
            def_yaml = gf.extend_props.get("definition_yaml") or gf.extend_props.get("collaboration_definition_yaml")
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
            req_kwargs["start_initial_run"] = bool(gf.extend_props.get("start_initial_run", True))
            # state_machine 群需 opening_message(task-loop panel),taskId = 任务ID
            _task_id = gf.extend_props.get("task_id")
            if _task_id:
                import json as _json
                req_kwargs["opening_message"] = {
                    "type": "panel",
                    "component": "partnerPanel.CollaborationRunView",
                    "params": _json.dumps({
                        "groupId": "{{bcs.group_id}}",
                        "sessionId": "{{bcs.session_id}}",
                        "runId": "{{bcs.run_id}}",
                        "groupName": "{{bcs.group_name}}",
                        "sessionName": "{{bcs.session_name}}",
                        "businessScene": "release_review",
                        "taskId": _task_id,
                        "apiBaseUrl": gf.extend_props.get("api_base_url") or "",
                    }),
                }
        if originator_bot_id:
            req_kwargs["originator"] = bcs_uuid(str(originator_bot_id))
        service_spec = gf.extend_props.get("service_spec")
        if service_spec:
            req_kwargs["service_spec"] = service_spec
        # BCN 事件回调订阅(创建协作群入参 event_subscriptions):BCS 把协作事件 CloudEvent 推到本后端
        # task 模块回调路径。sink.url = api_base_url + 回调路径(api_base_url 去尾斜杠)。
        # event_filters 按 collab_mode 分流:state_machine 订阅 state_machine.*;manager_worker/chat
        # 无状态机 run,去 state_machine.*、保留 group/session/task/message(§4 生命周期事件)。
        _api_base = gf.extend_props.get("api_base_url")
        if _api_base:
            _event_filters = (["group.*", "session.*", "task.*", "state_machine.*", "message.created"]
                              if mode == "state_machine"
                              else ["group.*", "session.*", "task.*", "message.created"])
            req_kwargs["event_subscriptions"] = [{
                "name": "group-webhook",
                "event_filters": _event_filters,
                "payload": {"mode": "metadata_only"},
                "sink": {
                    "type": "webhook",
                    "url": str(_api_base).rstrip("/") + _BCN_EVENT_CALLBACK_PATH,
                    "request_timeout_ms": 2000,
                },
            }]
        # 任务描述(目标)→ BCS 创建群的 context 字段。BCS ``resolve_session_topic`` 把 group.context
        # 兜底注入 <GroupContext> 的 `目标` 行(session input 为空时,如建群 BotJoined)。
        _task_context = gf.extend_props.get("task_context")
        # 任务验收 push 链路:协作群叶子派发期注入 loop_task_id,此处写入群 context,
        # 供 driver/owner bot 按 acceptance 段4 自验收后 push 回投 /callback/report
        # (loop_task_id 定位执行节点;backend 取本 TaskExecutor 的 api_base_url,不写死)。
        _loop_task_id = gf.extend_props.get("loop_task_id")
        if _loop_task_id and self._api_base_url:
            _task_context = ((_task_context or "") +
                             f"\n[task-loop] loop_task_id={_loop_task_id}; backend={self._api_base_url}")
        if _task_context:
            req_kwargs["context"] = _task_context
        req = BcsCreateGroupRequest(**req_kwargs)
        logger.info(
            "[task_executor] form_coop_group create_group request collab=%s driver_bot=%s participants=%s group_strategy=%s has_definition=%s has_bindings=%s",
            mode,
            req.driver_bot,
            req.participants,
            req.group_strategy,
            bool(req.collaboration_definition_yaml),
            bool(req.participant_bindings),
        )
        try:
            res = await self._bcs.create_group(req)
        except Exception:
            logger.exception(
                "[task_executor] form_coop_group create_group failed collab=%s driver_bot=%s participants=%s group_strategy=%s",
                mode, req.driver_bot, req.participants, req.group_strategy,
            )
            raise
        logger.info(
            "[task_executor] form_coop_group create_group succeeded collab=%s group_id=%s session_id=%s run_id=%s",
            mode, res.group_id, res.session_id, res.run_id,
        )
        self._group_meta[res.group_id] = {
            "collab_mode": mode, "gf": gf,
            "definition_ref": res.definition_ref, "session_id": res.session_id,
        }
        return res.group_id

    async def trigger_workflow(self, *, bot_id: str, message: str,
                               metadata: dict[str, Any] | None = None) -> BotSendResult:
        """Single-bot workflow trigger: send + register a SingleBotHandle; return BotSendResult."""
        sent = await self._bot.send_message(bot_id=bot_id, message=message,
                                            metadata=metadata or {})
        biz_task_id = (metadata or {}).get("biz_task_id", "")
        self._poller.register(SingleBotHandle(
            loop_task_id=f"{biz_task_id}::{biz_task_id}",  # root node_id == task_id
            run_id=sent.run_id, bot_id=bot_id,
            registered_at=time.monotonic(), session_id=sent.session_id,
        ))
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
            bcn=self._bcn, bot=self._bot,
            graph=self._graph,
            backend_url=self._api_base_url,
            skill_name=bbs_runner._BBS_SKILL_NAME,
        )

    @staticmethod
    def _state_machine_bindings(gf: GroupFormation) -> dict[str, dict[str, Any]]:
        """返回 workflow 逻辑 binding → 产品 Bot IDs；绝不使用 Bot ID 充当 binding key。"""
        explicit = gf.extend_props.get("participant_bindings")
        bindings: dict[str, dict[str, Any]] = {}
        if explicit is not None:
            if not isinstance(explicit, dict):
                raise BotIdentityResolutionError("participant_bindings must be a mapping")
            for binding, raw_spec in explicit.items():
                name = str(binding).strip()
                if not name:
                    raise BotIdentityResolutionError("participant binding name must not be empty")
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
