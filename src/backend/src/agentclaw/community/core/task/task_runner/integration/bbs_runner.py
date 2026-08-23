"""BBS 主动触发:bid→select→claim→dispatch。
升 BBS 可恢复态后,向 dream-mode roster 广播评估消息;从回复中选 completion_rate 最高的 bot;
引擎服务端 claim_bbs_owner;发任务消息给胜出 bot(best-effort,不抛)。"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_BBS_SKILL_NAME = "bbs-relay-single-task"
_BID_TIMEOUT = 170.0
_OVERALL_TIMEOUT = 180.0


async def notify(execution_graph, *, bcs, bot, graph, backend_url: str,
                 skill_name: str = _BBS_SKILL_NAME) -> None:
    """bid→select→claim→dispatch 给胜出 bot(best-effort,不抛)。"""
    task_id = execution_graph.task_id
    if bcs is None or bot is None:
        logger.info("[bbs-runner] skip: bcs/bot 缺失 task=%s", task_id)
        return
    try:
        roster = await bcs.list_bots_by_task_modes(dream=True, match="any")
    except Exception as exc:
        logger.warning("[bbs-runner] roster 取失败 task=%s:%s", task_id, exc)
        return
    if not roster:
        logger.info("[bbs-runner] 无 dream-mode bot task=%s,留可恢复态", task_id)
        return

    # Phase 1: bid (并发评估,3分钟超时)
    try:
        bid_results = await asyncio.wait_for(
            asyncio.gather(
                *[_bid_one(bot, r, execution_graph) for r in roster],
                return_exceptions=True,
            ),
            timeout=_OVERALL_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.info("[bbs-runner] bid 超时(180s)task=%s,取已回复", task_id)
        bid_results = []

    # 解析回复
    bids: list[dict] = []
    for result in bid_results:
        bid = _parse_bid(result)
        if bid and bid.get("completion_rate", 0) > 0:
            bids.append(bid)
    if not bids:
        logger.info("[bbs-runner] 无有效 bid task=%s,留可恢复态", task_id)
        return

    # Phase 2: select + claim + dispatch
    winner = max(bids, key=lambda b: b["completion_rate"])
    winner_bot_id = winner["bot_id"]
    try:
        graph.claim_bbs_owner(task_id, winner_bot_id)
    except Exception as exc:
        logger.warning("[bbs-runner] claim 失败 task=%s:%s", task_id, exc)
        return

    msg = _task_msg(skill_name, task_id, backend_url, winner_bot_id)
    try:
        await bot.send_message(
            bot_id=winner_bot_id, message=msg, metadata={"biz_task_id": task_id},
        )
    except Exception as exc:
        # send 失败 → 回收 claim
        from agentclaw.community.core.task.domain.models import TaskNodePatch
        graph.update_task_node_info(
            TaskNodePatch(task_id=task_id, node_id=task_id, extend_props_patch={"bbs_owner": None})
        )
        logger.warning("[bbs-runner] send 失败 bot=%s task=%s:%s", winner_bot_id, task_id, exc)


async def _bid_one(bot, rost_entry, execution_graph) -> dict | None:
    """一发一收:发给 bot 评估 prompt,取回复 content JSON {completion_rate}。"""
    task_id = execution_graph.task_id
    prompt = _bid_prompt(task_id, rost_entry.bot_id)
    try:
        run = await bot.send_and_wait_async(
            bot_id=rost_entry.bot_id, message=prompt,
            metadata={"biz_task_id": task_id}, timeout=_BID_TIMEOUT,
        )
    except Exception as exc:
        logger.warning("[bbs-runner] bid send_and_wait 失败 bot=%s:%s", rost_entry.bot_id, exc)
        return None
    return {"bot_id": rost_entry.bot_id, "run": run}


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


def _bid_prompt(task_id: str, bot_id: str) -> str:
    """让 bot 自评能完成多少,输出 JSON。"""
    return (
        f"请评估你能完成 task_id={task_id} 的多少剩余事项。\n"
        f"你自身 bot_id={bot_id}。\n"
        "请查看 dashboard (/api/v1/collaboration/tasks/dashboard?task_id="
        f"{task_id}) 了解根 goal 和已完成的叶子输出,\n"
        "自评你**能完成剩余事项的百分比**,输出 JSON: "
        '{"completion_rate": <0-100整数>}'
    )


def _task_msg(skill_name: str, task_id: str, backend_url: str, bot_id: str) -> str:
    """给胜出 bot 的任务消息(不含 task_spec——skill 自派生)。"""
    return (
        f"请用 {skill_name} 接力执行已升 BBS 的单子。\n"
        f"task_id={task_id};task API backend base url={backend_url};"
        f"你自身 bot_id={bot_id}。\n"
        "引擎已替你占根(bbs_owner已设),直接从 dashboard 读剩余事项 → attach → 执行 → result。"
    )
