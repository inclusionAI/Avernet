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
    AcceptanceResult, AcceptanceVerdict, Context, Goal, Metadata, RuntimeInfo, Status, TaskNode, TaskNodePatch, TaskSpec,
)

logger = logging.getLogger(__name__)

_BBS_SKILL_NAME = "bbs-relay-single-task"
_BID_TIMEOUT = 170.0
_OVERALL_TIMEOUT = 300.0


async def notify(execution_graph, *, bcn, bot, graph, backend_url: str,
                 skill_name: str = _BBS_SKILL_NAME,
                 on_bbs_report=None) -> None:
    """查询同时开启 claim/dream 的 provider Bot(复用统一 provider 身份 BcnService),再执行 bid→select→claim→dispatch。

    ``bcn``: :class:`BcnService`(由 DI 注入的任务模块普通消费依赖),复用 register/switch provider-bot 同源
    统一身份访问 ``GET /providers/{provider_id}/bots/by-task-modes``。``None``/异常 → 静默留可恢复态。
    """
    logger.info("[task][bbs_mode] bbs_runner, begin, task_id=%s, backend_url=%s, skill_name=%s", execution_graph.task_id, backend_url, skill_name)
    task_id = execution_graph.task_id
    if bcn is None or bot is None:
        logger.error("[task][bbs_mode] skip: bcn/bot 缺失 task=%s", task_id)
        return
    try:
        logger.info("[task][bbs_mode] bbs_runner, list_bots, task_id=%s", execution_graph.task_id)

        try:
            entries = await asyncio.to_thread(
                bcn.list_bots_by_task_modes, claim=True, dream=True, match="all",
            )
        except Exception as e:
            logger.error("[task][bbs_mode] bbs_runner, list_bots, task_id=%s, meet_exception=%s", execution_graph.task_id, e)
            return

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

    # 先增加一个bbs节点,RUNNING
    bbs_task_node = TaskNode(
        node_id=f"bbs-{uuid.uuid4().hex[:8]}",
        task_id=task_id,
        status=Status.RUNNING,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=task_id, title="BBS 接力", instruction=""),
            context=Context(background=""),
            goal=Goal(objective=msg, acceptances=[]),
        ),
        run_info=RuntimeInfo(
            run_mode="bbs",
            assignee=winner_bot_id
        ),
        node_run_graph=None
    )

    graph.add_task_nodes([bbs_task_node], task_id)
    logger.info("[task][bbs_mode] add_node, task_id=%s, nodes=%s", task_id, bbs_task_node)
    edges = [
        (task_id, bbs_task_node.node_id),
    ]
    graph.add_relations(task_id, edges)
    logger.info("[task][bbs_mode] add_edge, task_id=%s, edges=%s", task_id, edges)

    # 执行bbs，执行完后再更新
    try:
        logger.info("[task][bbs_mode] begin_rely_task, task_id=%s", task_id)
        task_result = await bot.send_and_wait_async(
            bot_id=winner_bot_id, message=msg, metadata={"biz_task_id": task_id},
            timeout=_OVERALL_TIMEOUT
        )
        logger.info("[task][bbs_mode] send_and_wait, task_id=%s, result_msg=%s", task_id, task_result)

        _bbs_output = task_result.get("result") if isinstance(task_result, dict) else task_result
        _bbs_session = task_result.get("session_id") if isinstance(task_result, dict) else ""
        _scoped_patch = TaskNodePatch(
            task_id=task_id,
            node_id=bbs_task_node.node_id,
            status=Status.DONE,
            # assignee=持有者身份:on_bbs_report 持有者校验要求 bbs_owner==patch.assignee
            # (claim_bbs_owner 已置根 bbs_owner=winner_bot_id;此处同源补齐,校验才放行)。
            assignee=winner_bot_id,
            output_patch={"output": _bbs_output},
            acceptance_result=AcceptanceResult(
                verdict=AcceptanceVerdict.DONE,
                acceptances_metric=list(),
                gaps=list(),
            ),
            extend_props_patch={
                "output": _bbs_output,
                "assignee_bot_id": winner_bot_id,
                "session_id": _bbs_session,
            },
        )
        if on_bbs_report is not None:
            # 收口走引擎:翻 scoped DONE → finally 释放 bbs_owner → _on_pass_collect 驱动根重算 gap
            # (plan(root)→_maybe_finish_graph/HUNG)。不再直写根 status=PLANNING(收敛自驱根态),
            # 也不再裸写 scoped —— 全部由 on_bbs_report 一次落入 SSOT 并触发收敛。
            await on_bbs_report(_scoped_patch)
            logger.info(
                "[task][bbs_mode] on_bbs_report 收口 task_id=%s node=%s",
                task_id, bbs_task_node.node_id,
            )
        else:
            # 无引擎回调(轻量/stub):遵守 bbs 模式不变量——只能改根节点状态 + graph 加关系,
            # 绝不改根节点 output(根 output 仅由 plan 算 gap / runner 执行完成 pull·push 收敛写入)。
            # 故此处仅落 scoped 接力节点终态(其自身执行产出,属 runner 回投)+ 根翻 PLANNING(可恢复态,
            # 等下段重 claim/升 BBS);不驱动收敛(需 engine 收口)、不直写根 output/extend_props.output。
            logger.warning(
                "[task][bbs_mode] on_bbs_report 未接入 task=%s:仅落 scoped 终态 + 根 PLANNING,"
                "不驱动收敛、不写根 output(排查 engine._build_executor/build_integration 漏传 on_bbs_report)",
                task_id,
            )
            graph.update_task_node_info(_scoped_patch)
            graph.update_task_node_info(
                TaskNodePatch(
                    task_id=task_id,
                    node_id=task_id,
                    status=Status.PLANNING,
                )
            )
        logger.info("[task][bbs_mode] finish_rely_task, task_id=%s, task_result=%s", task_id, task_result)
    except Exception as exc:
        logger.error("[task][bbs_mode] rely_task_meet_exception, task_id=%s, exception=%s", task_id, exc)
        # send 失败 → 回收 claim(释放 bbs_owner,避免泄漏挡住后续重升 BBS)。
        # 注:FAIL 收口(删 scoped 节点 + 图回可恢复态)语义待定,此处仅做无歧义的 claim 释放。
        graph.update_task_node_info(
            TaskNodePatch(
                task_id=task_id,
                node_id=task_id,
                status=Status.PLANNING,
                extend_props_patch={"bbs_owner": None},
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
