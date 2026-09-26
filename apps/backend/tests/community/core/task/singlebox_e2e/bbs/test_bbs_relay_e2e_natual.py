"""BBS 自然升 BBS + 自主接力 live e2e(金庸案例)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地 ``./scripts/singlebox.sh start all`` 起好 singlebox 后:

  SINGLEBOX_TASK_E2E=1 \
    src/backend/.venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e_natual.py -s

# 场景(对齐 spec §6.1 Scenario A / test_task_integration_e2e.py)

- owner bot(装 ``planning-arch`` + ``search``,通用规划/分解)+「金庸」(装 ``arch-analysis`` +
  ``bbs-relay-pickup``,BBS 接力执行者)。
- 任务目标:**整理某某某公司内部「基础架构」方向技术架构师**。自然链:owner 用通用 planning-arch 规划分解
  → 子任务"找架构师"经 search 派发 → 候选 bot 都不匹配 → ``on_miss@MAX_DEPTH`` 自然升 BBS
  (``bbs_mode=True``、根 ``PLANNING``、图空闲,即 spec §10.5 可恢复态)。
- 升 BBS 后本用例**唤醒一次金庸**:`bbs-relay-pickup` 由金庸自驱 6 步(发现 → claim → 自判 →
  attach → 用自身能力执行 → ``bbs/result`` 写回收口),不由用例代调 ``bbs/*`` 路由。
- 金庸自判 ``full`` → 一段接力做满剩余 → ``root_verified=true`` → 根 ``DONE`` → 图 ``DONE``。

# 与 ``test_bbs_relay_e2e_live.py`` 的区别(live 是演练接力 mechanics,natual 是自然链)

- live:in-process FastAPI+白盒直置 bbs 可恢复态 + 用例编排 claim/attach/result 复刻 6 步 loop。
- natual:**真实后端 ``POST /openapi/v1/collaboration/tasks/execute`` 走框架 planner/dispatch 自然升 BBS**;接力 loop 由金庸
  自身跑(``bbs-relay-pickup`` 用 ``exec``+HTTP 直调真实后端 ``/api/v1/collaboration/tasks/bbs/*``),用例只做
  provisioning + 提交 + 轮询 + 一次唤醒 + 断言。

# 设计约束

- owner 用独立 bot 名 ``task-owner-arch-bot`` + 通用 ``planning-arch`` skill,避免与
  ``test_task_integration_e2e.py`` 的 ``task-owner-bot``(装存储案例的 ``task-planning``)在同一
  singlebox 上共用 bot 造成 planning skill 串扰。
- 金庸的 ``arch-analysis`` 是真实 LLM 推理,经 ``SingleboxEngineAdapter`` live 调用(唤醒 + 接力中段
  执行都是 live);金庸本体由 ``SingleboxBotProvisioner`` 真实建 bot + 装 skill,幂等。
- ``SUB_DOMAINS`` 只取一个方向(基础架构)。**剧本刻意收窄为单一交付物**(3 位架构师名册)+ ``MAX_DEPTH=1``,
  使规划只展一层、拆出 **1~3 个扁平子任务**即停(不再像宽目标被切成一堆子方向);全 MISS 自然升 BBS,
  金庸自判 ``full`` 一段收口。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
import uuid
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    SingleboxBotProvisioner,
    SingleboxEngineAdapter,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "146836")
_TIMEOUT = float(os.environ.get("SINGLEBOX_TASK_E2E_TIMEOUT", "1500"))
# 独立 owner 名:避免与 test_task_integration_e2e.py 的 task-owner-bot(装存储案例 planning)共用 bot
_OWNER_BOT_NAME = "task-owner-arch-bot"
_JY_BOT_NAME = "金庸"

SKILLS_DIR = Path(__file__).parent / "../skills"
_ARCH_PLANNING_SKILL = str(SKILLS_DIR / "planning-arch")
_SEARCH_SKILL = str(SKILLS_DIR / "search")
_ARCH_SKILL = str(SKILLS_DIR / "arch-analysis")
# bbs-relay-pickup skill 落在 spec 目录下(非 src/backend/skills);
# test 文件在 <repo>/src/backend/tests/community/core/task/singlebox_e2e/bbs/ ,parents[6] = <repo>/src/backend
_BBS_SKILL = str(
    Path(__file__).resolve().parents[6]
    / "specs" / "2026-08-09-task-goal-driven-task-runner-bbs" / "bbs-relay-pickup"
)

# 任务目标:整理某某某公司「基础架构」方向 3 位核心架构师名册;单一交付物 + MAX_DEPTH=1 → 规划只展一层、
# 拆出 1~3 个扁平子任务即停,全 MISS 自然升 BBS,金庸一段接力收口。
TASK_ID = f"t_arch_{uuid.uuid4().hex[:6]}"
SUB_DOMAINS = ["基础架构"]
_BBS_MAX_DEPTH = 3  # 单方向一段收口;金庸自判 full 一次唤醒即可

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

# 单次 dashboard 读超时:engine 在 threading.RLock 内跨长 LLM 调用,query_task_dashboard 同锁,
# 写路径持锁跑规划/派发时读会排队(可远超常规读时延)。短超时 + 外层轮询重试,熬过一次性排队。
_DASH_TIMEOUT = 60.0


async def _get_dashboard(cli: httpx.AsyncClient, task_id: str) -> dict | None:
    """读 ``/openapi/v1/collaboration/tasks/dashboard``;一次性排队/断网时返 ``None`` 供外层轮询重试(不直接 fail 用例)。

    engine 的 ``on_miss``/``on_execute`` 等在 per-task ``threading.RLock`` 内跨长 LLM,而
    ``query_task_dashboard`` 同锁,写路径持锁规划期间读会阻塞。用短超时把"单次卡死"降级为重试,
    避免一次长排队直接撞穿 httpx 客户端超时(曾以 ReadTimeout 终结整个用例)。
    """
    try:
        r = await cli.get(
            f"{_BACKEND}/openapi/v1/collaboration/tasks/dashboard",
            params={"task_id": task_id},
            timeout=_DASH_TIMEOUT,
        )
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        print(f"[dashboard] 读超时/网络异常,稍后重试:{exc!r}")
        return None
    if r.status_code != 200:
        print(f"[dashboard] 非 200(status={r.status_code}),稍后重试:{r.text[:120]!r}")
        return None
    return r.json().get("data") or {}


def _execute_body(owner_id: str) -> dict:
    """``POST /openapi/v1/collaboration/tasks/execute`` 请求体(TaskInfoDTO):整理「基础架构」方向架构师。

    剧本收敛为**单一交付物**(一张基础架构方向的小型架构师名册),配 ``MAX_DEPTH=1`` 使规划只展一层:
    - 目标粒度收窄到"3 位核心架构师姓名/角色/职责",且 instruction 明示"单一交付的人才名册,不要按子方向
      再拆分",避免 planner 把"基础架构"再切成中间件/存储/云原生/可观测/安全… 等一堆子方向(MAx_DEPTH=1
      时即使切也只展一层、且 depth-1 miss 直接走 ``miss_depth_exhausted`` 可恢复,不 re-plan 嵌套)。
    - 预期:owner 规划出 1~3 个扁平子任务 → 普通派发全 MISS(无此类 bot)→ 自然升 BBS(根 PLANNING 可恢复)
      → 金庸 claim 时 recover 清掉 HUNG 死分支 → 挂 1 个 bbs scoped 节点自驱 → 收口 SUCCESS。
    """
    sub = SUB_DOMAINS[0]
    return {
        "task_spec": {
            "metadata": {
                "task_id": TASK_ID,
                "title": f"整理某某某公司内部「{sub}」方向技术架构师名册",
                "instruction": (
                    f"整理某某某公司内部「{sub}」方向的 **3 位核心技术架构师**,"
                    f"给出每位架构师的姓名/角色 + 主要职责。这是一个**单一交付的人才名册**,"
                    f"不要按子方向(中间件/存储/云原生/可观测/安全 等)再拆分。"
                ),
            },
            "context": {"background": "某某某公司内部技术架构师梳理", "extend_props": {}},
            "goal": {
                "objective": f"整理某某某公司内部「{sub}」方向 3 位核心技术架构师(姓名/角色 + 职责)",
                "acceptances": [
                    {"id": "ac1", "description": f"给出「{sub}」方向 3 位架构师的姓名/角色 + 职责"},
                ],
            },
        },
        "source_type": "bot",
        "owner_bot_id": owner_id,
        # MAX_DEPTH=1:只展一层,depth-1 miss 直走 miss_depth_exhausted 可恢复(不 re-plan 嵌套);
        # 规划出 1~3 个扁平子任务即停,避免拆成太多。
        "execution_config": {"MAX_DEPTH": 1, "BBS_MAX_DEPTH": _BBS_MAX_DEPTH},
    }


def _wake_prompt(jy_bot_id: str) -> str:
    """唤醒金庸自驱 bbs-relay-pickup(用例只唤醒不代调 bbs/*)。

    只交代用哪个 skill 接单 + 必要定位信息(task_id / backend url / 自身 bot_id),
    不复述 skill 内部的 6 步流程——那是 bbs-relay-pickup SKILL.md 该做的。

    必须传入金庸自身的 bot_id:bot 不知 provisioning 给它的真实 bot_id,不传会误填引擎身份
    (如 ``openclaw-agent``),导致节点 ``assignee`` 与 provisioning bot_id 不一致。
    """
    return (
        "请用 bbs-relay-pickup skill 接力执行已自然升 BBS 的单子。\n"
        f"task_id={TASK_ID};task API backend base url={_BACKEND};"
        f"你(金庸)自身 bot_id={jy_bot_id}(claim/attach/result 的 bot_id 字段填它)。"
    )


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox live e2e")
class TestBbsRelayE2ENatual(unittest.TestCase):
    def test_feng_qingyang_relays_architects_via_natural_bbs(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) provisioning(幂等建 bot + 装 skill):
        #    owner 装通用 planning-arch + search(规划/分解 → 派发 MISS → 自然升 BBS);
        #    金庸 装 arch-analysis + bbs-relay-pickup(BBS 接力执行者)。
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        owner_id = await prov.create_bot(bot_name=_OWNER_BOT_NAME)
        await prov.install_skills(owner_id, [_ARCH_PLANNING_SKILL, _SEARCH_SKILL])
        jy_id = await prov.create_bot(bot_name=_JY_BOT_NAME)
        await prov.install_skills(jy_id, [_ARCH_SKILL, _BBS_SKILL])
        await prov._aclose()
        print(f"[provision] owner={owner_id} ← planning-arch+search ; "
              f"金庸={jy_id} ← arch-analysis+bbs-relay-pickup")

        # 2) live adapter:用于唤醒金庸自驱 bbs-relay-pickup(真实 LLM 推理)
        adapter = SingleboxEngineAdapter(backend_base_url=_BACKEND, user_id=_USER_ID)

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 3) POST /openapi/v1/collaboration/tasks/execute → backend 进程内真实 engine 推进:owner 规划 → search 派发 →
            #    候选不匹配 → on_miss@MAX_DEPTH 自然升 BBS(bbs_mode=True / 根 PLANNING / 图空闲)。
            r = await cli.post(f"{_BACKEND}/openapi/v1/collaboration/tasks/execute", json=_execute_body(owner_id))
            r.raise_for_status()
            print(f"[execute] {r.json().get('message')} data={r.json().get('data')}")

            # 等自然升 BBS:Poll 直到 bbs_mode 置 true(或全图 SUCCESS / 超时)
            g: dict = {}
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                g = await _get_dashboard(cli, TASK_ID)
                if g is None:
                    await asyncio.sleep(5.0)
                    continue  # engine 持锁跑规划导致读排队,稍后重试
                snap = [
                    (t.get("node_id"), t.get("status"),
                     (t.get("run_info") or {}).get("run_mode") or "",
                     str((t.get("run_info") or {}).get("assignee") or "")[:24])
                    for t in g.get("tasks") or []
                ]
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} "
                      f"bbs_mode={(g.get('extend_props') or {}).get('bbs_mode')} nodes={snap}")
                if (g.get("extend_props") or {}).get("bbs_mode"):
                    # 已自然升 BBS:打印升 BBS 落点(可恢复态 vs 图级 HUNG),供定位 §10.5 seam
                    _ep = g.get("extend_props") or {}
                    _nodes = {t["node_id"]: t for t in g.get("tasks") or []}
                    _root = _nodes.get(TASK_ID)
                    print(
                        f"[escalated] ⭐ 已自然升 BBS! task={TASK_ID} "
                        f"graph={g.get('status')} loop_round={g.get('loop_round')} "
                        f"bbs_mode={_ep.get('bbs_mode')} bbs_relay_count={_ep.get('bbs_relay_count')} "
                        f"hung_reason(图)={_ep.get('hung_reason')} "
                        f"root.status={(_root or {}).get('status')} "
                        f"node_count={len(g.get('tasks') or [])}"
                    )
                    for _t in g.get("tasks") or []:
                        _ri = _t.get("run_info") or {}
                        print(f" 已自然升BBS  - {_t.get('node_id'):28} {_t.get('status'):9} "
                              f"mode={_ri.get('run_mode') or '-':5} "
                              f"reason={(_ri.get('extend_props') or {}).get('hung_reason') or '-'}")
                    break  # 已自然升 BBS
                if g.get("status") == "SUCCESS":
                    break  # 未升 BBS 已闭环(异常路径,留待断言揭出)
                await asyncio.sleep(5.0)

            # 4) 断言自然升 BBS 落到可恢复态(spec §10.5):bbs_mode=True + 根 PLANNING(可委托,非图级 HUNG)
            self.assertTrue(
                (g.get("extend_props") or {}).get("bbs_mode"),
                f"任务未自然升 BBS(bbs_mode 未置 true);看快照定位 planning/dispatch。graph={g.get('status')}",
            )
            nodes = {t["node_id"]: t for t in g.get("tasks") or []}
            root = nodes.get(TASK_ID)
            self.assertIsNotNone(root, "根节点未出现")

            # 5) 唤醒金庸自驱 bbs-relay-pickup:用例只唤醒,不代调 bbs/* 路由。
            #    一次唤醒 = 一段接力;未收口且图空闲则再唤醒,上限 BBS_MAX_DEPTH 次。
            wake_prompt = _wake_prompt(jy_id)
            wakes = 0
            while g.get("status") not in ("DONE", "HUNG") and wakes < _BBS_MAX_DEPTH:
                wakes += 1
                print(f"[wake#{wakes}] 唤醒金庸自驱 bbs-relay-pickup ...")
                try:
                    run = await adapter.send_and_wait_async(
                        bot_id=jy_id, message=wake_prompt, timeout=600.0
                    )
                    status = run.get("status")
                    content = (run.get("result") or {}).get("content") or ""
                    print(f"[wake#{wakes}] status={status} content[:300]={content[:300]!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[wake#{wakes}] adapter 异常:{exc!r}")
                # 唤醒后轮询,等接力写回落地 / 图收口 / 图空闲可再唤醒
                sub_deadline = time.monotonic() + 300.0
                while time.monotonic() < sub_deadline:
                    g = await _get_dashboard(cli, TASK_ID)
                    if g is None:
                        await asyncio.sleep(5.0)
                        continue  # 同上:engine 持锁排队,稍后重试
                    snap = [
                        (t.get("node_id"), t.get("status"),
                         (t.get("run_info") or {}).get("run_mode") or "",
                         str((t.get("run_info") or {}).get("assignee") or "")[:24])
                        for t in g.get("tasks") or []
                    ]
                    print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} "
                          f"bbs_owner={(nodes_first_ext(g,'bbs_owner'))} nodes={snap}")
                    if g.get("status") in ("DONE", "HUNG"):
                        break
                    # 图空闲(无 RUNNING + 无人占根)→ 本次唤醒写回已落,可决定再唤醒
                    busy = any((t.get("status") == "RUNNING") for t in g.get("tasks") or [])
                    held = (g.get("extend_props") or {}).get("bbs_owner")
                    if not busy and not held:
                        break
                    await asyncio.sleep(5.0)

        # 6) 断言:自然升 BBS + 金庸自主接力收口图 SUCCESS
        try:
            await adapter._aclose()
        except Exception:
            pass

        self.assertEqual(g.get("status"), "SUCCESS", f"全图未闭环 DONE:status={g.get('status')}")
        nodes = {t["node_id"]: t for t in g.get("tasks") or []}
        self.assertEqual(nodes[TASK_ID]["status"], "SUCCESS", "根未 SUCCESS")
        self.assertTrue((g.get("extend_props") or {}).get("bbs_mode"), "图未置 bbs_mode")

        bbs_nodes = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "bbs" and t["node_id"] != TASK_ID
        ]
        self.assertGreaterEqual(
            len(bbs_nodes), 1,
            f"无金庸自驱的 bbs scoped 节点(检查 bbs-relay-pickup 是否被唤醒执行);"
            f"nodes={[t.get('node_id') for t in g.get('tasks') or []]}",
        )
        for n in bbs_nodes:
            ri = n.get("run_info") or {}
            self.assertEqual(n.get("status"), "SUCCESS", f"scoped 未 SUCCESS:{n.get('node_id')}")
            self.assertEqual(
                ri.get("assignee"), jy_id,
                f"scoped 非 金庸 接力:{n.get('node_id')} assignee={ri.get('assignee')}",
            )
            self.assertTrue(
                (ri.get("output") or {}).get("architects"),
                f"scoped 缺架构师 checkpoint:{n.get('node_id')}",
            )
        print(f"[final] graph={g.get('status')} 金庸接力段={len(bbs_nodes)} 唤醒={wakes} 根=SUCCESS")


def nodes_first_ext(g: dict, key: str) -> str:
    """取图 extend_props 上的 key(如 bbs_owner),缩略打印用。"""
    v = (g.get("extend_props") or {}).get(key)
    return str(v)[:24] if v else "-"


if __name__ == "__main__":
    unittest.main()