"""BBS 主动触发:bid→select→claim→dispatch。

升 BBS 可恢复态后,向 dream bot roster 广播评估消息;从回复中选 completion_rate 最高的 bot;
引擎服务端 claim_bbs_owner;发任务消息给胜出 bot(best-effort,不抛)。

TEMP(e2e):roster 取数临时改走 ``BotPublicServiceProtocol.search_public_bots_by_keyword``,
按关键字 ``_BBS_BID_DREAM_KEYWORD`` 命中命名的 e2e dream bot(替代需 provider_id +
task_dream_mode PATCH 的 ``bcs.list_bots_by_task_modes``)。**全局生效**——prod/corp 的
BBS active-relay roster 路径在位期间失效(只搜 e2e 命名 bot),跑完 e2e 需回退,或换成
per-profile 的 ``BbsRosterPort`` 抽象(singlebox 走 keyword、prod 走 bcs.provider_id 过滤)。"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any
from agentclaw.community.core.task.domain.models import (
    AcceptanceResult, AcceptanceVerdict, Goal, RuntimeInfo, Status, TaskNode, TaskNodePatch, TaskSpec,
)

logger = logging.getLogger(__name__)

_BBS_SKILL_NAME = "bbs-relay-single-task"
_BID_TIMEOUT = 170.0
_OVERALL_TIMEOUT = 300.0


async def notify(execution_graph, *, bcn, bot, graph, backend_url: str,
                 skill_name: str = _BBS_SKILL_NAME) -> None:
    """查询同时开启 claim/dream 的 provider Bot(复用统一 provider 身份 BcnService),再执行 bid→select→claim→dispatch。

    ``bcn``: :class:`BcnService`(由 DI 注入的任务模块普通消费依赖),复用 register/switch provider-bot 同源
    统一身份访问 ``GET /providers/{provider_id}/bots/by-task-modes``。``None``/异常 → 静默留可恢复态。
    """
    logger.info("[task][bbs_mode] bbs_runner, begin, task_id=%s, backend_url=%s, skill_name=%s", execution_graph.task_id, backend_url, skill_name)
    task_id = execution_graph.task_id
    if bcn is None or bot is None:
        logger.info("[task][bbs_mode] skip: bcn/bot 缺失 task=%s", task_id)
        return
    try:
        entries = await asyncio.to_thread(
            bcn.list_bots_by_task_modes, claim=True, dream=True, match="all",
        )

        logger.info("[task][bbs_mode] bbs_runner, begin, task_id=%s, entries=%d,%s", execution_graph.task_id, len(entries), entries)
        if len(entries) > 10:
            entries = entries[:10]
    except Exception as exc:
        logger.warning("[task][bbs_mode] roster 取失败 task=%s:%s", task_id, exc)
        return
    if not entries:
        logger.info("[task][bbs_mode] 无 dream bot 命中 task=%s,留可恢复态", task_id)
        return

    logger.info("[task][bbs_mode] roster 取成功 task=%s, num=%d", task_id, len(entries))
    # Phase 1: bid (并发评估,3分钟超时)
    try:
        bid_results = await asyncio.wait_for(
            asyncio.gather(
                *[_bid_one(bot, r, execution_graph) for r in entries],
                return_exceptions=True,
            ),
            timeout=_OVERALL_TIMEOUT,
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
    except Exception as exc:
        logger.warning("[task][bbs_mode] claim 失败 task=%s:%s", task_id, exc)
        return
    logger.info("[task][bbs_mode] bid winner is=%s, task_id=%s", winner_bot_id, task_id)

    msg = _task_msg(skill_name, execution_graph, backend_url, winner_bot_id)
    try:
        logger.info("[task][bbs_mode] begin_rely_task, task_id=%s", task_id)
        task_result = await bot.send_and_wait_async(
            bot_id=winner_bot_id, message=msg, metadata={"biz_task_id": task_id},
            timeout=_OVERALL_TIMEOUT
        )
        logger.info("[task][bbs_mode] send_and_wait, task_id=%s, result_msg=%s", task_id, task_result)

        bbs_task_node = TaskNode(
            node_id = f"bbs-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            status=Status.DONE,
            task_spec=TaskSpec(),
            run_info=RuntimeInfo(),
            node_run_graph=None
        )
        bbs_task_node.task_spec = TaskSpec()
        bbs_task_node.task_spec.goal = Goal()
        bbs_task_node.task_spec.goal.objective = msg
        bbs_task_node.run_info.run_mode = "bbs"

        graph.add_task_nodes([bbs_task_node], task_id)
        logger.info("[task][bbs_mode] send_and_wait, task_id=%s, add_bbs_new_node=%s", task_id, bbs_task_node)

        # 更新根节点
        graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                status=Status.PLANNING
            )
        )
        logger.info("[task][bbs_mode] finish_rely_task, task_id=%s, task_result=%s", task_id, task_result)
    except Exception as exc:
        logger.error("[task][bbs_mode] rely_task_meet_exception, task_id=%s, exception=%s", task_id, exc)

        bbs_task_node = TaskNode(
            node_id = f"bbs-{uuid.uuid4().hex[:8]}",
            task_id=task_id,
            status=Status.FAILED,
            task_spec=TaskSpec(),
            run_info=RuntimeInfo(),
            node_run_graph=None
        )
        bbs_task_node.task_spec = TaskSpec()
        bbs_task_node.task_spec.goal = Goal()
        bbs_task_node.task_spec.goal.objective = msg
        bbs_task_node.run_info.run_mode = "bbs"

        graph.add_task_nodes([bbs_task_node], task_id)
        logger.warning("[task][bbs_mode] send_and_wait, task_id=%s, add_bbs_new_failed_node=%s", task_id, bbs_task_node)

        # send 失败 → 回收 claim
        graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                status=Status.PLANNING
            )
        )
        logger.warning("[task][bbs_mode] send 失败 bot=%s task=%s:%s", winner_bot_id, task_id, exc)


async def _bid_one(bot, rost_entry, execution_graph) -> dict | None:
    """一发一收:发给 bot 评估 prompt,取回复 content JSON {completion_rate}。"""
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
    """从 _bid_one 返回 {bot_id, run} 中解析 completion_rate。"""
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
    bot_id = bid_result.get("bot_id", "")
    return {"bot_id": bot_id, "completion_rate": int(rate)}


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
    可选深读 dashboard URL。返回格式 ``{"completion_rate": <0-100整数>}``。
    """
    task_id = getattr(execution_graph, "task_id", "") or ""
    snapshot = _build_task_snapshot(execution_graph)
    return (
        "[bbs-bid] 请基于以下任务快照自评:你能完成该任务**剩余事项的百分比**(0-100)。\n"
        f"你自身 bot_id={bot_id};task_id={task_id}(仅作引用)。\n"
        "快照含根 goal/验收项/已 DONE 子节点产出/gaps;据 goal 与已完成产出算剩余 gap,"
        "基于自身能力(不联网)自评能补完的剩余事项占比,输出 JSON: "
        '{"completion_rate": <0-100整数>}\n'
        f"任务态快照\n{json.dumps(snapshot, ensure_ascii=False)}\n"
    )


def _task_msg(skill_name: str, execution_graph, backend_url: str, bot_id: str) -> str:
    """给胜出 bot 的任务消息:内联任务态快照,skill 据快照归纳剩余事项(免读 dashboard)→ attach → 执行 → result。

    task_id/backend_url/bot_id 仍保留供步骤② attach / 步骤④ result 的 API 调用;dashboard 仅作可选兜底深读。
    """
    task_id = getattr(execution_graph, "task_id", "") or ""
    snapshot = _build_task_snapshot(execution_graph)
    return (
        f"请为我执行一下任务。\n"
        "**任务态快照已内联**(下方 JSON):含根 goal(objective+acceptances)、instruction、background、"
        "done_children(已 DONE 子节点+产出)、gaps、loop_round。**直接据快照归纳剩余事项**"
        "(剩余 = goal.acceptances 全集 − done_children 产出并集,再按 gaps 细化),无需先读 dashboard;\n"
        "然后请为我完成基于剩余事项\n"
        f"任务态快照如下：\n{json.dumps(snapshot, ensure_ascii=False)}"
    )
