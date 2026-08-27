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


class TaskExecutor:
    def __init__(self, *, bot, bcs, formatter, context, sink, poller,
                 identity_resolver=None, graph=None,
                 api_base_url: str = "", bcn: BcnService | None = None,
                 bot_token_provider=None) -> None:
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
        self._bot_token_provider = bot_token_provider  # driver-bot session_token 取数(直读 bcs_bots);None→不发 Bearer
        self._graph = graph
        self._api_base_url = api_base_url
        self._group_meta: dict[str, dict[str, Any]] = {}  # group_id -> {collab_mode, gf, definition_ref, session_id}

    async def dispatch(self, toDoTaskList: list[TaskNode]) -> list[bool]:
        sem = asyncio.Semaphore(_DISPATCH_CONCURRENCY)
        logger.info("[task][task-executor] dispatch 入口 nodes=%s modes=%s",
                    [n.node_id for n in toDoTaskList], [n.run_info.run_mode for n in toDoTaskList])

        async def _one(node: TaskNode) -> bool:
            mode = node.run_info.run_mode
            if mode == "bbs":
                logger.info("[task][task_executor] bbs node dispatched (no-op): task=%s node=%s assignee=%s",
                            node.task_id, node.node_id, node.run_info.assignee)
                return True
            if mode == "single_bot":
                logger.info("[task][task-executor] >>> 投递 single_bot task=%s node=%s bot=%s → ensure_grant+send_message",
                            node.task_id, node.node_id, node.run_info.assignee)
                return await self._dispatch_single_bot(node, sem)
            if mode == "coop_group":
                logger.info("[task][task-executor] >>> 投递 coop_group task=%s node=%s → form_coop_group(create_group)",
                            node.task_id, node.node_id)
                return await self._dispatch_coop_group(node, sem)
            logger.warning("[task][task-executor] node=%s 未知 run_mode=%s → 不投递", node.node_id, mode)
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
            except (OpenApiAuthError, OpenApiBadRequestError) as exc:
                logger.warning(
                    "[task][task-executor] single_bot 派发失败(OpenAPI %s)task=%s node=%s bot=%s: %s "
                    "→ 留 PENDING 交 harness;grep [task][openapi_bot] 看具体哪步(http)失败",
                    type(exc).__name__, node.task_id, node.node_id, bot_id, exc,
                )
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
                mode, bot_ids, referenced_ids,
            )
            raise
        logger.info(
            "[task][task_executor] form_coop_group identities resolved collab=%s resolved=%s",
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
            # §4 任务协作群事件:内联挂 event_subscriptions,BCS 主动推 §4 事件回 Avernet 回调路由,
            # 激活既有 apply_manager_worker_event → task_callback.execution_graph(audit 快照)+ converge_by_session。
            # 鉴权 = HMAC + 既有 caller_bot_token(Bearer driver-bot);require_human 由 Bearer(+HMAC) 兜,无 cookie
            # (见 specs/2026-08-26-task-execute-group-kind-and-manager-worker-event-push/spec.md §4.3)。
            if self._api_base_url:
                req_kwargs["event_subscriptions"] = [{
                    "name": "avernet-manager-worker",
                    "event_filters": ["group.created", "session.created",
                                      "task.assigned", "task.completed", "session.completed"],
                    "payload": {"mode": "full"},
                    "sink": {"type": "webhook",
                             "url": f"{self._api_base_url}/api/v1/collaboration/tasks/callback/report",
                             "request_timeout_ms": 10000},
                }]
            else:
                logger.warning(
                    "[task][manager_worker] _api_base_url 未配,跳过 event_subscriptions(sink.url 需绝对地址);poller 兜底收敛")
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
                # BCS 契约:opening_message.params 必须是 JSON object,不能字符串化
                # (见 ocb-public/src/bcs/docs/custom-collaboration-opening-message-integration-guide.md §4:
                # params = 传给业务组件的 JSON object)。字符串化会被真实 BCS 判
                # "data did not match any variant of untagged enum OpeningMessage" 422。
                req_kwargs["opening_message"] = {
                    "type": "panel",
                    "component": "partnerPanel.CollaborationRunView",
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
        if originator_bot_id:
            req_kwargs["originator"] = bcs_uuid(str(originator_bot_id))
        service_spec = gf.extend_props.get("service_spec")
        if service_spec:
            req_kwargs["service_spec"] = service_spec
        # event_subscriptions:仅 manager_worker 分支(上方)内联挂 §4 订阅推回;state_machine/chat 不挂。
        # 鉴权:driver-bot session_token 作 caller 身份(``Authorization: Bearer``);manager_worker 挂订阅时 BCS 走
        # require_human,由 Bearer(+HMAC) 兜过(无 cookie,见 spec §4.3);state_machine/chat 走 no-sub 分支建群。
        # 终态收敛:result poller 轮询 get_state_machine_run / get_group 兜底(与 §4 推送双兜底,同进 on_report 幂等)。
        if self._bot_token_provider is not None:
            req_kwargs["caller_bot_token"] = self._bot_token_provider.get_token(
                req_kwargs.get("driver_bot") or "")
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
                gf.extend_props.get("manager_bot_id") or (gf.bot_ids[0] if gf.bot_ids else "")
            )
            _reporter_role = "master/manager"
        elif mode == "state_machine":
            _reporter_bot_id = str(gf.bot_ids[0] if gf.bot_ids else "")
            _reporter_role = "master/BCS driver"
        else:
            _reporter_bot_id = str(
                gf.extend_props.get("originator_bot_id") or (gf.bot_ids[0] if gf.bot_ids else "")
            )
            _reporter_role = "拉群 Bot/driver"
        _task_objective = str(gf.extend_props.get("task_objective") or _task_context or "")
        _task_instruction = str(gf.extend_props.get("task_instruction") or "")
        if _task_objective or _task_instruction or _loop_task_id:
            req_kwargs["context"] = (
                "[task-execute]\n"
                "execution_mode=coop_group\n"
                f"reporter_bot_id={_reporter_bot_id}\n"
                f"reporter_role={_reporter_role}\n"
                "只有 reporter_bot_id 对应的 Bot（本群唯一 master/driver）可以调用 "
                "task-loop 的任务验收(acceptance)逻辑，逐条检查当前节点 goal.acceptances，"
                "汇总完整执行输出，并主动回投验收结果；其它 Bot 只提供产出，不得重复回调。\n"
                f"目标:{_task_objective}\n"
                f"指令:{_task_instruction}\n"
                f"验收标准:{json.dumps(_acceptances, ensure_ascii=False)}\n"
                f"任务上下文:{_task_context or ''}"
            )
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
            res = await self._bcs.create_group(req)
        except Exception:
            logger.exception(
                "[task][task_executor] form_coop_group create_group failed collab=%s driver_bot=%s participants=%s group_strategy=%s",
                mode, req.driver_bot, req.participants, req.group_strategy,
            )
            raise
        logger.info(
            "[task][task_executor] form_coop_group create_group succeeded collab=%s group_id=%s session_id=%s run_id=%s",
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
