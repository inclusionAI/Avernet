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
    AcceptanceResult, AcceptanceVerdict, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskNodePatch, TaskSpec,
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


async def notify(execution_graph, *, bcn, bot, graph, backend_url: str,
                 skill_name: str = _BBS_SKILL_NAME,
                 on_bbs_report=None, group_executor=None) -> None:
    """查询开启 claim 的 provider Bot,再执行 bid→select→claim→dispatch。

    ``bcn``: :class:`BcnService`(由 DI 注入的任务模块普通消费依赖),复用 register/switch provider-bot 同源
    统一身份访问 ``GET /providers/{provider_id}/bots/by-task-modes``。roster 查询失败时有界重试,
    最终仍失败则静默留可恢复态。``dream`` 不参与筛选,BBS 候选只要求 ``claim=True``。
    """
    logger.info("[task][bbs_mode] bbs_runner, begin, task_id=%s, backend_url=%s, skill_name=%s", execution_graph.task_id, backend_url, skill_name)
    task_id = execution_graph.task_id
    if bcn is None or bot is None:
        logger.error("[task][bbs_mode] skip: bcn/bot 缺失 task=%s", task_id)
        return
    logger.info("[task][bbs_mode] bbs_runner, list_bots, task_id=%s", execution_graph.task_id)
    entries = await _list_claim_bots(bcn, task_id)
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
    # Phase 1: bid (并发评估,3分钟超时)
    try:
        bid_results = await asyncio.wait_for(
            asyncio.gather(
                *[_bid_one(bot, r, execution_graph) for r in entries],
                return_exceptions=True,
            ),
            timeout=_OVERALL_TIMEOUT_4_BID,
        )
        logger.info("[task][bbs_mode] task_id=%s, bid_results=%s", task_id, bid_results)
    except asyncio.TimeoutError:
        logger.error("[task][bbs_mode] bid 超时(180s)task=%s,取已回复", task_id)
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
        graph.claim_bbs_owner(task_id, winner_bot_id)
        bbs_claim_at = int(time.time() * 1000)
    except Exception as exc:
        logger.warning("[task][bbs_mode] claim 失败 task=%s:%s", task_id, exc)
        return
    logger.info("[task][bbs_mode] bid winner is=%s, task_id=%s", winner_bot_id, task_id)

    _group_enabled = _singlebot_2_group_switch(graph, task_id)
    actual_run_mode = "coop_group" if (_group_enabled and group_executor is not None) else "bbs"

    # 先增加一个bbs节点,PENDING
    bbs_task_node = TaskNode(
        node_id=f"bbs-{uuid.uuid4().hex[:8]}",
        task_id=task_id,
        status=Status.PENDING,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title=winner.get("title"), instruction=""),
            context=Context(background=""),
            goal=Goal(objective=winner.get("goal"), acceptances=[]),
        ),
        run_info=RuntimeInfo(
            run_mode=actual_run_mode,
            assignee=winner_bot_id,
            start_time=bbs_claim_at,
            extend_props={"actual_run_mode": "bbs", "bbs_claim_at": bbs_claim_at},
        ),
        node_run_graph=None
    )

    # BBS is a recovery execution under a HUNG root. Creating the scoped node
    # must not make the parent look actively planned before the relay completes;
    # on_bbs_report is the single path that resumes parent planning.
    graph.add_task_nodes([bbs_task_node], task_id, mark_parent_planning=False)
    logger.info("[task][bbs_mode] add_node, task_id=%s, nodes=%s", task_id, bbs_task_node)
    edges = [
        (task_id, bbs_task_node.node_id),
    ]
    graph.add_relations(task_id, edges)
    logger.info("[task][bbs_mode] add_edge, task_id=%s, edges=%s", task_id, edges)

    # 任务msg
    msg = _task_msg(skill_name, execution_graph, backend_url, winner_bot_id, bbs_task_node.task_id, bbs_task_node.node_id,
                    title=winner.get("title", ""), goal=winner.get("goal", ""), )

    # 执行bbs，执行完后再更新
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
                    task_instruction=msg,
                    deadline_monotonic=time.monotonic() + _OVERALL_TIMEOUT_4_DOT,
                )
            except Exception as exc:  # noqa: BLE101 建群/轮询失败 → 回退 send_and_wait(不阻断 single bot 投递)
                logger.error(
                    "[task][bbs_mode] manager_worker 群执行失败 → 回退 send_and_wait task=%s: %s",
                    task_id, exc,
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

        logger.warning(
        "[task][bbs_mode] on_bbs_report 未接入 task=%s:仅落 scoped 终态 + 保持根 HUNG, 不驱动收敛、不写根 output(排查 engine._build_executor/build_integration 漏传 on_bbs_report)",
        task_id
        )
        graph.update_task_node_info(_scoped_patch)
        logger.info("[task][bbs_mode] finish_rely_task, task_id=%s, task_result=%s, scoped_patch=%s", task_id, task_result, _scoped_patch)
    except Exception as exc:
        logger.error("[task][bbs_mode] rely_task_meet_exception, task_id=%s, exception=%s", task_id, exc)
        # send 失败 → 回收 claim(释放 bbs_owner,避免泄漏挡住后续重升 BBS)。
        # send 失败不产生 BBS 回投，保留节点与运行记录，仅释放 claim。
        graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                # BBS dispatch failure releases the claim but must preserve the
                # root HUNG recovery state. PLANNING would surface as EXECUTING
                # and falsely make a terminal child set look active again.
                extend_props_patch={"bbs_owner": None},
            )
        )
        logger.warning("[task][bbs_mode] send 失败 bot=%s task=%s:%s", winner_bot_id, task_id, exc)


async def _list_claim_bots(bcn, task_id: str) -> list[dict]:
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
                    match="all",
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
                return []
            delay = _ROSTER_RETRY_DELAY * (2 ** (attempt - 1))
            logger.warning(
                "[task][bbs_mode] list_bots failed task=%s attempt=%d/%d "
                "retry_in=%.1fs error=%s",
                task_id, attempt, _ROSTER_MAX_RETRIES, delay, exc,
            )
            await asyncio.sleep(delay)
    return []


async def _bid_one(bot, rost_entry, execution_graph) -> dict | None:
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
            "title": n.task_spec.metadata.title,
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
        "instruction": spec.metadata.instruction,
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


def _task_msg(skill_name: str, execution_graph, backend_url: str, bot_id: str, task_id: str, node_id: str,
              *, title: str = "", goal: str = "") -> str:
    """给胜出 bot 的任务消息:内联任务态快照,skill 据快照归纳剩余事项(免读 dashboard)→ attach → 执行 → result。

    task_id/backend_url/bot_id 仍保留供步骤② attach / 步骤④ result 的 API 调用;dashboard 仅作可选兜底深读。
    ``title``/``goal`` 为胜出 bot bid 时承诺能完成的这部分事项的标题/目标,带入执行消息(置顶)让 bot
    按其中标的子任务范围执行,而非仅据全量快照自行裁剪。
    """

    report_instruction = """
  curl -X POST \
    'https://agentclawengine-pre.alipay.com/api/v1/collaboration/tasks/callback/report' \
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
    """

    snapshot = _build_task_snapshot(execution_graph)
    parts = [f"我自主判断要接力执行的任务信息如下：task_id={task_id}, node_id={node_id} \n"]
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
        f"注意：执行完之之后，一定要主动上报执行结果。\n"
        f"任务态快照如下：\n{json.dumps(snapshot, ensure_ascii=False)}"
    )
    return "".join(parts)
