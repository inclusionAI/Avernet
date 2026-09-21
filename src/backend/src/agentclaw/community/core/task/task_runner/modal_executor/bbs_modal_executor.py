"""BBS 主动触发:bid→select→claim→dispatch。

根节点进入 runtime HUNG 后升 BBS 可恢复态,向 claim-enabled bot roster 广播评估消息;
从回复中选 completion_rate 最高的 bot;引擎服务端 claim_bbs_owner;发任务消息给
胜出 bot(best-effort,不抛)。roster 查询只要求 ``claim=True``,并对临时失败做有界
超时与重试。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any
from agentclaw.community.core.task.domain.models import (
    Context, Goal, RuntimeInfo, Status, TaskCallbackData, TaskNode, TaskNodePatch,
    TaskSpec, task_spec_instruction, task_spec_title,
)
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    format_task_node_business_instruction,
)

logger = logging.getLogger(__name__)

_BBS_SKILL_NAME = "bbs-relay-single-task"
_BID_TIMEOUT = 170.0
_OVERALL_TIMEOUT_4_BID = 300.0
_OVERALL_TIMEOUT_4_DOT = 600.0
_ROSTER_TIMEOUT = 60.0
_ROSTER_MAX_RETRIES = 3
_ROSTER_RETRY_DELAY = 1.0


def _singlebot_2_group_switch(graph, task_id: str) -> bool:
    """镜像 ``TaskExecutor._singlebot_2_group_enabled`` 默认 True 语义:从 graph 级
    ``extend_props.execution_config.singlebot_2_group`` 读;graph 异常/缺键 → True(默认走旁路)。
    ``execution_config`` 经 BBS 升级保留(task_graph_service 初始化写入 graph.extend_props)。"""
    try:
        snapshot = graph.query_task_dashboard(task_id)
    except Exception:  # noqa: BLE101 graph 不可用 → 默认 True(走旁路)
        logger.info("[task][bbs_mode] singlebot_2_group 开关:graph 查询失败 → 默认 True task=%s", task_id)
        return True
    cfg = (getattr(snapshot, "extend_props", None) or {}).get("execution_config") or {}
    if not isinstance(cfg, dict):
        return True
    val = cfg.get("singlebot_2_group", True)
    return val if isinstance(val, bool) else str(val).lower() not in ("false", "0", "no", "none", "")


def _resolve_owner_user_id_from_graph(graph, task_id: str) -> str:
    """BBS 任务 owner(graph 级 ``owner_user_id``,task_graph_service 初始化写入、query_task_dashboard 返回);
    缺则空串 → ``form_coop_group`` 不拉人类观察者(经理 bot 自身执行)。"""
    try:
        snapshot = graph.query_task_dashboard(task_id)
    except Exception:  # noqa: BLE101 graph 不可用 → 不拉观察者
        logger.info("[task][bbs_mode] resolve owner 查询失败 task=%s → 不拉人类观察者", task_id)
        return ""
    owner = (getattr(snapshot, "extend_props", None) or {}).get("owner_user_id")
    return str(owner) if owner else ""


def _trajectory_node_id(execution_graph, target_node_id: str | None) -> str:
    return str(target_node_id or execution_graph.task_id)


def _trajectory_node(execution_graph, target_node_id: str | None):
    node_id = _trajectory_node_id(execution_graph, target_node_id)
    return next(
        (node for node in getattr(execution_graph, "tasks", []) or []
         if str(getattr(node, "node_id", "")) == node_id),
        None,
    )


def _emit_bbs_trajectory(
    task_context_service,
    execution_graph,
    target_node_id: str | None,
    action_result: str,
    *,
    exception: Exception | None = None,
    details: dict[str, Any] | None = None,
    error_msg: str | None = None,
    boost_reason: str | None = None,
) -> None:
    """Write BBS milestones/errors through the task-context trajectory facade."""
    if task_context_service is None:
        return
    task_id = str(execution_graph.task_id)
    node = _trajectory_node(execution_graph, target_node_id)
    node_status = getattr(node, "status", None)
    run_info = getattr(node, "run_info", None)
    extend_props = getattr(run_info, "extend_props", None) or {}
    try:
        attempt = int(extend_props.get("harness_retries", 0) or 0)
    except (TypeError, ValueError):
        attempt = 0
    ext_info: dict[str, Any] = {"execution_mode": "bbs", "phase": "bbs_modal"}
    if details:
        ext_info.update(details)
    error_type = None
    if exception is not None:
        exception_type = type(exception).__name__
        ext_info["exception_type"] = exception_type
        error_type = (
            "transport_error"
            if isinstance(exception, (TimeoutError, ConnectionError))
            else "unclassified"
        )
        error_msg = str(exception)[:2000]
    try:
        task_context_service.emit_trajectory_event(
            task_id,
            _trajectory_node_id(execution_graph, target_node_id),
            "execute",
            action_result=action_result,
            error_type=error_type,
            error_msg=error_msg,
            ext_info=ext_info,
            status_from=node_status,
            status_to=node_status,
            attempt=attempt,
            boost_reason=boost_reason or action_result,
            now_ms=int(time.time() * 1000),
        )
    except Exception as exc:  # noqa: BLE001 trajectory is observational only
        logger.warning(
            "[task][trajectory] BBS 轨迹发射失败 task=%s action=%s: %s",
            task_id, action_result, exc,
        )


async def _notify_impl(execution_graph, *, bcn, bot, graph, backend_url: str,
                 skill_name: str = _BBS_SKILL_NAME,
                 on_bbs_report=None, group_executor=None,
                 target_node_id: str | None = None, task_context_service=None) -> None:
    """查询开启 claim 的 provider Bot,再执行 bid→select→claim→dispatch。

    ``bcn``: :class:`BcnService`(由 DI 注入的任务模块普通消费依赖),复用 register/switch provider-bot 同源
    统一身份访问 ``GET /providers/{provider_id}/bots/by-task-modes``。roster 查询失败时有界重试,
    最终仍失败则静默留可恢复态。``dream`` 不参与筛选,BBS 候选只要求 ``claim=True``。
    """
    logger.info("[task][bbs_mode] bbs_runner, begin, task_id=%s, backend_url=%s, skill_name=%s", execution_graph.task_id, backend_url, skill_name)
    task_id = execution_graph.task_id
    _emit_bbs_trajectory(
        task_context_service, execution_graph, target_node_id, "bbs_entered",
        boost_reason="进入BBS模态",
    )
    if bcn is None or bot is None:
        logger.error("[task][bbs_mode] skip: bcn/bot 缺失 task=%s", task_id)
        return
    logger.info("[task][bbs_mode] bbs_runner, list_bots, task_id=%s", execution_graph.task_id)
    entries = await _list_claim_bots(
        bcn, task_id,
        on_error=lambda exc: _emit_bbs_trajectory(
            task_context_service, execution_graph, target_node_id,
            "bbs_roster_failed", exception=exc,
        ),
    )
    logger.info(
        "[task][bbs_mode] bbs_runner, begin, task_id=%s, entries=%d,%s",
        execution_graph.task_id, len(entries), entries,
    )
    if len(entries) > 10:
        entries = entries[:10]
    if not entries:
        logger.info("[task][bbs_mode] 无 claim bot 命中 task=%s,留可恢复态", task_id)
        return

    logger.info("[task][bbs_mode] roster 取成功 task=%s, num=%d", task_id, len(entries))
    _emit_bbs_trajectory(
        task_context_service, execution_graph, target_node_id,
        "bbs_bid_broadcast", details={"candidate_count": len(entries)},
        boost_reason="广播竞价",
    )
    # Phase 1: bid (并发评估,3分钟超时)
    try:
        bid_results = await asyncio.wait_for(
            asyncio.gather(
                *[_bid_one(
                    bot, r, execution_graph,
                    on_error=lambda exc, bot_id=r.get("bot_id"): _emit_bbs_trajectory(
                        task_context_service, execution_graph, target_node_id,
                        "bbs_bid_failed", exception=exc,
                        details={"bot_id": bot_id},
                    ),
                ) for r in entries],
                return_exceptions=True,
            ),
            timeout=_OVERALL_TIMEOUT_4_BID,
        )
        logger.info("[task][bbs_mode] task_id=%s, bid_results=%s", task_id, bid_results)
    except asyncio.TimeoutError as exc:
        logger.error("[task][bbs_mode] bid 超时(180s)task=%s,取已回复", task_id)
        _emit_bbs_trajectory(
            task_context_service, execution_graph, target_node_id,
            "bbs_bid_failed", exception=exc,
        )
        bid_results = []

    # 解析回复
    bids: list[dict] = []
    for result in bid_results:
        bid = _parse_bid(result)
        if bid and bid.get("completion_rate", 0) > 0:
            bids.append(bid)
    if not bids:
        logger.info("[task][bbs_mode] 无有效 bid task=%s,留可恢复态", task_id)
        return

    # Phase 2: select + claim + dispatch
    winner = max(bids, key=lambda b: b["completion_rate"])
    winner_bot_id = winner["bot_id"]
    try:
        graph.report(TaskCallbackData(data={
            "report_type": "BBS_CLAIM",
            "payload": {
                "task_id": task_id,
                "node_id": target_node_id,
                "bot_id": winner_bot_id,
                "claim_id": (
                    f"auto-{task_id}-{target_node_id}-{winner_bot_id}"
                    if target_node_id else None
                ),
            },
        }))
        bbs_claim_at = int(time.time() * 1000)
    except Exception as exc:
        logger.warning("[task][bbs_mode] claim 失败 task=%s:%s", task_id, exc)
        _emit_bbs_trajectory(
            task_context_service, execution_graph, target_node_id,
            "bbs_claim_failed", exception=exc,
        )
        return
    logger.info("[task][bbs_mode] bid winner is=%s, task_id=%s", winner_bot_id, task_id)

    _group_enabled = _singlebot_2_group_switch(graph, task_id)
    actual_run_mode = "coop_group" if (_group_enabled and group_executor is not None) else "bbs"

    if target_node_id is not None:
        # Relay claim is node-local and already transitions the target to RUNNING.
        refreshed = graph.query_task_dashboard(task_id)
        bbs_task_node = next(
            (item for item in refreshed.tasks if item.node_id == target_node_id),
            None,
        )
        if bbs_task_node is None or bbs_task_node.status != Status.RUNNING:
            logger.warning(
                "[task][bbs_mode] relay target missing/not running after claim task=%s node=%s",
                task_id,
                target_node_id,
            )
            return
        logger.info(
            "[task][bbs_mode] relay target claimed task=%s node=%s winner=%s",
            task_id,
            target_node_id,
            winner_bot_id,
        )
    else:
        # Centralized mode creates a new scoped BBS node.
        bbs_task_node = TaskNode(
            node_id=f"bbs-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            status=Status.PENDING,
            task_spec=TaskSpec(
                context=Context(title=str(winner.get("title") or ""), background=""),
                goal=Goal(objective=str(winner.get("goal") or ""), acceptances=[]),
            ),
            run_info=RuntimeInfo(
                run_mode=actual_run_mode,
                assignee=winner_bot_id,
                start_time=bbs_claim_at,
                extend_props={"actual_run_mode": "bbs", "bbs_claim_at": bbs_claim_at},
            ),
            node_run_graph=None
        )
        graph.report(TaskCallbackData(data={
            "report_type": "ADD_NODES",
            "payload": {
                "task_id": task_id,
                "nodes": [bbs_task_node],
                "parent_node_id": task_id,
                "mark_parent_planning": False,
            },
        }))
        logger.info("[task][bbs_mode] add_node, task_id=%s, nodes=%s", task_id, bbs_task_node)

    # 任务msg:分布式 Relay 的 BBS 认领者仍是一条普通接力棒，只允许接收
    # 同一的 context→EXECUTION_RESULT→PLAN_RESULT→search→DISPATCH_RESULT→dispatch
    # 闭环；中心化 legacy BBS 才保留旧的 status/output 一次性上报协议。
    relay_inputs = None
    if _relay_mode(execution_graph, target_node_id):
        relay_inputs = _relay_task_inputs(
            execution_graph=execution_graph,
            node=bbs_task_node,
            reason=winner.get("relay_reason", ""),
            title=winner.get("title", ""),
            goal=winner.get("goal", ""),
        )
        msg = _relay_task_msg(
            inputs=relay_inputs,
            backend_url=backend_url,
            bot_id=winner_bot_id,
            node=bbs_task_node,
        )
    else:
        msg = _task_msg(
            skill_name,
            execution_graph,
            backend_url,
            winner_bot_id,
            bbs_task_node.task_id,
            bbs_task_node.node_id,
            winner.get("relay_reason", ""),
            title=winner.get("title", ""),
            goal=winner.get("goal", ""),
        )

    # 执行bbs，执行完后再更新
    _emit_bbs_trajectory(
        task_context_service, execution_graph, target_node_id,
        "bbs_execution_started",
        details={"winner_bot_id": winner_bot_id, "execution_mode": actual_run_mode},
        boost_reason=f"竞价胜出，开始执行。竞价胜出的bot是{winner_bot_id}，胜出原因是{winner.get("relay_reason")}",
    )
    try:
        logger.info("[task][bbs_mode] begin_rely_task, task_id=%s, msg=%s", task_id, msg)
        if _group_enabled and group_executor is not None:
            _owner_user_id = _resolve_owner_user_id_from_graph(graph, task_id)
            logger.info(
                "[task][bbs_mode] manager_worker 群执行 task=%s node=%s winner=%s owner=%s",
                task_id, bbs_task_node.node_id, winner_bot_id, _owner_user_id,
            )
            try:
                task_result = await group_executor(
                    task_id=task_id,
                    node_id=bbs_task_node.node_id,
                    winner_bot_id=winner_bot_id,
                    owner_user_id=_owner_user_id,
                    task_instruction=(
                        str(relay_inputs["instruction"])
                        if relay_inputs is not None
                        else msg
                    ),
                    deadline_monotonic=time.monotonic() + _OVERALL_TIMEOUT_4_DOT,
                    relay_execution=relay_inputs is not None,
                    task_objective=(
                        str(relay_inputs["objective"])
                        if relay_inputs is not None
                        else ""
                    ),
                    acceptances=(
                        relay_inputs["acceptances"]
                        if relay_inputs is not None
                        else []
                    ),
                    upstream_outputs=(
                        relay_inputs["upstream_outputs"]
                        if relay_inputs is not None
                        else {}
                    ),
                    relay_blackboard=(
                        relay_inputs["relay_blackboard"]
                        if relay_inputs is not None
                        else None
                    ),
                )
            except Exception as exc:  # noqa: BLE101 建群/轮询失败 → 回退 send_and_wait(不阻断 single bot 投递)
                logger.error(
                    "[task][bbs_mode] manager_worker 群执行失败 → 回退 send_and_wait task=%s: %s",
                    task_id, exc,
                )
                _emit_bbs_trajectory(
                    task_context_service, execution_graph, target_node_id,
                    "bbs_group_execution_failed", exception=exc,
                    details={"winner_bot_id": winner_bot_id},
                )
                task_result = await bot.send_and_wait_async(
                    bot_id=winner_bot_id, message=msg, metadata={"biz_task_id": task_id},
                    timeout=_OVERALL_TIMEOUT_4_DOT,
                )
        else:
            logger.error(
                "[task][bbs_mode] send_and_wait 直发 task=%s node=%s winner=%s group_enabled=%s has_group_executor=%s",
                task_id, bbs_task_node.node_id, winner_bot_id, _group_enabled, group_executor is not None,
            )
            task_result = await bot.send_and_wait_async(
                bot_id=winner_bot_id, message=msg, metadata={"biz_task_id": task_id},
                timeout=_OVERALL_TIMEOUT_4_DOT,
            )
        logger.info("[task][bbs_mode] exec_done, task_id=%s, result=%s", task_id, task_result)

        _bbs_output = task_result.get("result") if isinstance(task_result, dict) else task_result
        _bbs_session = task_result.get("session_id") if isinstance(task_result, dict) else ""
        _scoped_patch = TaskNodePatch(
            task_id=task_id,
            node_id=bbs_task_node.node_id,
            status=Status.RUNNING,
            # assignee=持有者身份:on_bbs_report 持有者校验要求 bbs_owner==patch.assignee
            # (claim_bbs_owner 已置根 bbs_owner=winner_bot_id;此处同源补齐,校验才放行)。
            assignee=winner_bot_id,
            output_patch={"output": _bbs_output},
            extend_props_patch={
                "actual_run_mode": "bbs",
                "assignee_bot_id": winner_bot_id,
                "session_id": _bbs_session,
                "relay_reason": winner.get("relay_reason", ""),
            }
        )

        if target_node_id is None and on_bbs_report is not None:
            await on_bbs_report(_scoped_patch)
        else:
            if target_node_id is None:
                logger.warning(
                    "[task][bbs_mode] on_bbs_report 未接入 task=%s:仅落 scoped 运行事实，保持根 HUNG",
                    task_id,
                )
            # Relay target stays RUNNING until its task-loop Skill reports
            # EXECUTION_RESULT; centralized fallback records the scoped output.
            graph.report(TaskCallbackData(data={
                "report_type": "NODE_PATCH",
                "payload": {"patch": _scoped_patch},
            }))
        logger.info("[task][bbs_mode] finish_rely_task, task_id=%s, task_result=%s, scoped_patch=%s", task_id, task_result, _scoped_patch)
    except Exception as exc:
        logger.error("[task][bbs_mode] rely_task_meet_exception, task_id=%s, exception=%s", task_id, exc)
        _emit_bbs_trajectory(
            task_context_service, execution_graph, target_node_id,
            "bbs_execution_failed", exception=exc,
            details={"winner_bot_id": winner_bot_id},
        )
        # send 失败 → 回收 claim(释放 bbs_owner,避免泄漏挡住后续重升 BBS)。
        # send 失败不产生 BBS 回投，保留节点与运行记录，仅释放 claim。
        graph.report(TaskCallbackData(data={
            "report_type": "NODE_PATCH",
            "payload": {"patch": TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                # BBS dispatch failure releases the claim but must preserve the
                # root HUNG recovery state. PLANNING would surface as EXECUTING
                # and falsely make a terminal child set look active again.
                extend_props_patch={"bbs_owner": None},
            )},
        }))
        logger.warning("[task][bbs_mode] send 失败 bot=%s task=%s:%s", winner_bot_id, task_id, exc)


async def notify(execution_graph, *, bcn, bot, graph, backend_url: str,
                 skill_name: str = _BBS_SKILL_NAME,
                 on_bbs_report=None, group_executor=None,
                 target_node_id: str | None = None, task_context_service=None) -> None:
    """Run the BBS flow and record uncaught notify errors in task trajectory."""
    try:
        await _notify_impl(
            execution_graph=execution_graph,
            bcn=bcn,
            bot=bot,
            graph=graph,
            backend_url=backend_url,
            skill_name=skill_name,
            on_bbs_report=on_bbs_report,
            group_executor=group_executor,
            target_node_id=target_node_id,
            task_context_service=task_context_service,
        )
    except Exception as exc:  # noqa: BLE001 preserve existing notify propagation
        _emit_bbs_trajectory(
            task_context_service, execution_graph, target_node_id,
            "bbs_notify_failed", exception=exc,
        )
        raise


async def _list_claim_bots(bcn, task_id: str, *, on_error=None) -> list[dict]:
    """查询 claim-enabled roster with bounded timeout/retry.

    Empty results are valid and are not retried. Only request failures and
    timeouts retry, so an empty roster does not create an external query storm.
    """
    for attempt in range(1, _ROSTER_MAX_RETRIES + 1):
        try:
            entries = await asyncio.wait_for(
                asyncio.to_thread(
                    bcn.list_bots_by_task_modes,
                    claim=True,
                    dream=None,
                    match="all",
                    visibility="public",
                ),
                timeout=_ROSTER_TIMEOUT,
            )
            return entries if isinstance(entries, list) else []
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 roster is a best-effort BBS input
            if attempt >= _ROSTER_MAX_RETRIES:
                logger.error(
                    "[task][bbs_mode] list_bots exhausted task=%s attempts=%d error=%s",
                    task_id, attempt, exc,
                )
                if on_error is not None:
                    on_error(exc)
                return []
            delay = _ROSTER_RETRY_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "[task][bbs_mode] list_bots failed task=%s attempt=%d/%d "
                "retry_in=%.1fs error=%s",
                task_id, attempt, _ROSTER_MAX_RETRIES, delay, exc,
            )
            await asyncio.sleep(delay)
    return []


async def _bid_one(bot, rost_entry, execution_graph, *, on_error=None) -> dict | None:
    """一发一收:发给 bot 评估 prompt,取回复 content JSON {completion_rate, relay_reason, title, goal}。"""
    task_id = execution_graph.task_id
    bot_id = rost_entry["bot_id"]
    prompt = _bid_prompt(execution_graph, bot_id)
    try:
        run = await bot.send_and_wait_async(
            bot_id=bot_id, message=prompt,
            metadata={"biz_task_id": task_id}, timeout=_BID_TIMEOUT,
        )
        logger.info("[task][bbs_mode] bid send_and_wait 成功 bot=%s，%s", bot_id, run)
    except Exception as exc:
        logger.error("[task][bbs_mode] bid send_and_wait 失败 bot=%s:%s", bot_id, exc)
        if on_error is not None:
            on_error(exc)
        return None
    return {"bot_id": bot_id, "run": run}


def _parse_bid(bid_result: Any) -> dict | None:
    """从 _bid_one 返回 {bot_id, run} 中解析 completion_rate + relay_reason + title + goal(bot 未给则空串)。"""
    if not isinstance(bid_result, dict):
        return None
    run = bid_result.get("run")
    if not isinstance(run, dict):
        return None
    status = str(run.get("status") or "").upper()
    if status != "COMPLETED":
        return None
    content = (run.get("result") or {}).get("content") or ""
    if not content:
        return None
    try:
        obj = json.loads(content) if isinstance(content, str) else content
    except (json.JSONDecodeError, TypeError):
        try:
            from agentclaw.community.core.task.domain.json_extract import extract_json
            obj = extract_json(content)
        except Exception:
            return None
    if not isinstance(obj, dict):
        return None
    rate = obj.get("completion_rate")
    if not isinstance(rate, (int, float)) or rate <= 0:
        return None
    reason = obj.get("relay_reason")
    reason = reason if isinstance(reason, str) else ""
    title = obj.get("title")
    title = title if isinstance(title, str) else ""
    goal = obj.get("goal")
    goal = goal if isinstance(goal, str) else ""
    bot_id = bid_result.get("bot_id", "")
    return {
        "bot_id": bot_id,
        "completion_rate": int(rate),
        "relay_reason": reason,
        "title": title,
        "goal": goal,
    }


def _build_task_snapshot(execution_graph) -> dict:
    """构造任务态快照(bid 自评 + dispatch 共用;参考 task_plan._compose_planning_prompt 的 snapshot,以**根节点**为 target)。

    根 node_id == execution_graph.task_id(升 BBS 的那条单子)。``done_children`` = 根的已 DONE 结构子
    及其产出(本地复刻 task_plan._done_children 逻辑——用 execution_graph.relations + RelationType.DEPENDENCY,
    不跨模块 import task_plan 私有助手,保持 task_runner 自洽)。供 bot 据 goal/验收/已产出/gaps 自评可完成
    剩余事项百分比。字段零 case 知识。根节点缺失(理论不该发生)→ 极简快照,不抛(不阻断 BBS dispatch)。
    """
    from agentclaw.community.core.task.domain.models import RelationType, Status

    task_id = getattr(execution_graph, "task_id", "") or ""
    tasks = list(getattr(execution_graph, "tasks", []) or [])
    root = next((n for n in tasks if getattr(n, "node_id", None) == task_id), None)
    loop_round = getattr(execution_graph, "loop_round", 0)
    if root is None:
        return {"task_id": task_id, "loop_round": loop_round, "note": "root node missing"}
    spec = root.task_spec
    goal = spec.goal
    ctx = spec.context
    acc = root.run_info.acceptance_result if root.run_info else None
    gaps = list(acc.gaps) if acc else []
    child_ids = [
        r.dst_id for r in (getattr(execution_graph, "relations", []) or [])
        if r.src_id == root.node_id and r.type == RelationType.DEPENDENCY
    ]
    done_children = [
        {
            "node_id": n.node_id,
            "title": task_spec_title(n.task_spec),
            "output": (n.run_info.output if n.run_info else None),
        }
        for n in tasks
        if n.node_id in child_ids and n.status == Status.DONE
    ]
    return {
        "task_id": task_id,
        "node_id": root.node_id,
        "status": str(root.status),
        "goal": goal.objective,
        "instruction": task_spec_instruction(spec),
        "background": ctx.background if ctx else None,
        "acceptances": [
            {"id": a.id, "description": a.description} for a in goal.acceptances
        ],
        "done_children": done_children,
        "gaps": gaps,
        "loop_round": loop_round,
    }


def _bid_prompt(execution_graph, bot_id: str) -> str:
    """让 bot 据内联任务快照自评能完成多少剩余事项,输出 JSON。

    snapshot 内联(参考 task_plan._compose_planning_prompt),免 bot 再读 dashboard;``task_id`` 仅作引用,
    可选深读 dashboard URL。返回格式
    ``{"completion_rate": <0-100整数>, "relay_reason": "<可完成理由与依据>",
       "title": "<你能完成的这部分事项的标题>", "goal": "<你能完成的这部分事项的目标/目标成果>"}``。
    title/goal 圈定该 bot 承诺能完成的那部分事项,后续带入真正执行的任务消息。
    """
    task_id = getattr(execution_graph, "task_id", "") or ""
    snapshot = _build_task_snapshot(execution_graph)
    return (
        "[bbs-bid] 请基于以下任务快照自评:你能完成该任务**剩余事项的百分比**(0-100),"
        "并给出 relay_reason(你为什么觉得自己能完成该任务、依据是什么),"
        "同时给出**你能完成的这一部分事项**的 title(该部分事项的标题)与 goal(该部分事项的目标/目标成果)。\n"
        f"你自身 bot_id={bot_id};task_id={task_id}(仅作引用)。\n"
        "快照含根 goal/验收项/已 DONE 子节点产出/gaps;据 goal 与已完成产出算剩余 gap,"
        "基于自身能力(不联网)自评能补完的剩余事项占比,并说明判断依据(可完成理由 + 对应 snapshot 字段),"
        "再圈定你承诺能完成的那部分事项并给出其 title 与 goal,输出 JSON: "
        '{"completion_rate": <0-100整数>, "relay_reason": "<可完成理由与依据>", '
        '"title": "<你能完成的这部分事项的标题>", "goal": "<你能完成的这部分事项的目标/目标成果>"}\n'
        f"任务态快照\n{json.dumps(snapshot, ensure_ascii=False)}\n"
    )


def _relay_mode(execution_graph, target_node_id: str | None) -> bool:
    """Use Relay protocol for a node-local BBS claim in distributed Relay mode."""
    if target_node_id is not None:
        return True
    config = (getattr(execution_graph, "extend_props", None) or {}).get(
        "execution_config", {}
    )
    return isinstance(config, dict) and config.get("orchestration_mode") == "relay"


def _relay_claim_instruction(
    *, title: str, goal: str, reason: str,
) -> str:
    lines: list[str] = []
    if title:
        lines.append(f"BBS认领范围: {title}")
    if goal:
        lines.append(f"BBS认领目标: {goal}")
    if reason:
        lines.append(f"BBS认领依据: {reason}")
    return "；".join(lines)


def _relay_task_inputs(
    *,
    execution_graph,
    node: TaskNode,
    reason: str,
    title: str = "",
    goal: str = "",
) -> dict[str, Any]:
    """Return the Relay task facts used by direct delivery and group delivery."""
    snapshot = _build_task_snapshot(execution_graph)
    claim_instruction = _relay_claim_instruction(
        title=title, goal=goal, reason=reason
    )
    instruction = task_spec_instruction(node.task_spec)
    if claim_instruction:
        instruction = f"{claim_instruction}\n{instruction}"
    upstream = {
        str(item.get("node_id")): item.get("output")
        for item in snapshot.get("done_children", [])
        if isinstance(item, dict) and item.get("node_id")
    }
    return {
        "objective": str(node.task_spec.goal.objective),
        "instruction": instruction,
        "acceptances": [
            {"id": acceptance.id, "description": acceptance.description}
            for acceptance in node.task_spec.goal.acceptances
        ],
        "upstream_outputs": upstream,
        "relay_blackboard": {
            "snapshot": snapshot,
            "bbs_claim_title": title,
            "bbs_claim_goal": goal,
            "bbs_claim_reason": reason,
        },
    }


def _relay_task_msg(
    *,
    inputs: dict[str, Any],
    backend_url: str,
    bot_id: str,
    node: TaskNode,
) -> str:
    """Build the unified Relay closure instruction for a node-local BBS claimant."""
    return format_task_node_business_instruction(
        task_id=str(node.task_id),
        node_id=str(node.node_id),
        backend=backend_url,
        objective=str(inputs["objective"]),
        instruction=str(inputs["instruction"]),
        acceptances=inputs["acceptances"],
        upstream_outputs=inputs["upstream_outputs"],
        reporter_bot_id=str(bot_id),
        executor_bot_ids=[str(bot_id)],
        relay_execution=True,
        relay_blackboard=inputs["relay_blackboard"],
    )


def _task_msg(
    skill_name: str,
    execution_graph,
    backend_url: str,
    bot_id: str,
    task_id: str,
    node_id: str,
    reason: str,
    *,
    title: str = "",
    goal: str = "",
) -> str:
    """给胜出 bot 的任务消息:内联任务态快照,skill 据快照归纳剩余事项(免读 dashboard)→ attach → 执行 → result。

    task_id/backend_url/bot_id 仍保留供步骤② attach / 步骤④ result 的 API 调用;dashboard 仅作可选兜底深读。
    ``title``/``goal`` 为胜出 bot bid 时承诺能完成的这部分事项的标题/目标,带入执行消息(置顶)让 bot
    按其中标的子任务范围执行,而非仅据全量快照自行裁剪。
    """

    callback_url = f"{backend_url.rstrip('/')}/api/v1/collaboration/tasks/callback/report"
    report_instruction = """
  curl -X POST \
    '__TASK_CALLBACK_URL__' \
    -H 'Content-Type: application/json' \
    -d '{
      "task_id": "71502914-0caf-4866-952c-49d60d159607",
      "node_id": "bbs-b614056c",
      "status": "SUCCESS",
      "output": "存储行业尽调报告已完成,覆盖全部 5 项验收标准……",
      "acceptance_result": {
        "verdict": "DONE",
        "acceptances_metric": [
          {"id": "ac1", "passed": true, "summary": "投资价值已明确,给出 ★★★★☆ 评级"}
        ],
        "gaps": []
      },
      "extend_props": {}
    }'
    """.replace("__TASK_CALLBACK_URL__", callback_url)

    snapshot = _build_task_snapshot(execution_graph)
    parts = [f"我自主判断要接力执行的任务信息如下：task_id={task_id}, node_id={node_id} \n"]
    if reason:
        parts.append(f"我能够接力执行的原因主要是：{reason}\n")
    parts.append("执行步骤：1、执行任务 2、通过post接口上报结果\n")
    parts.append(f"上报方法参考如下示例：\n {report_instruction} \n")
    if title or goal:
        if title:
            parts.append(f"- title: {title}\n")
        if goal:
            parts.append(f"- goal: {goal}\n")
    parts.append(
        "**任务态快照已内联**(下方 JSON):含根 goal(objective+acceptances)、instruction、background、"
        "done_children(已 DONE 子节点+产出)、gaps、loop_round。**直接据快照归纳剩余事项**"
        "(剩余 = goal.acceptances 全集 − done_children 产出并集,再按 gaps 细化),无需先读 dashboard;\n"
        f"注意：执行完之后，一定要主动通过上述 {callback_url} 接口上报执行结果。\n"
        f"任务态快照如下：\n{json.dumps(snapshot, ensure_ascii=False)}"
    )
    return "".join(parts)
