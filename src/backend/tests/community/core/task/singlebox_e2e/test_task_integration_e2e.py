"""任务目标驱动执行框架 — singlebox 真实端到端集成用例(三种执行模态全覆盖,到图 DONE)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地起后端 singlebox 时**务必设**:

  SINGLEBOX_BCS_DOUBLE=1            # 协作群走 _DoubleBcsClient(确定 completed + success 回投,不依赖真群聊时序)
  SINGLEBOX_TASK_E2E=1              # 启用真实 e2e

  SINGLEBOX_BCS_DOUBLE=1 SINGLEBOX_TASK_E2E=1 \
    .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_task_integration_e2e.py -s

三种执行模态(三种 run_mode)都在单案例中覆盖:
  - ``single_bot``  : N_overview / N_compete / N_practice_bbs / N_report   → owner 真实 bot 经 singlebox 引擎执行 → poller 回投 PASS。
  - ``coop_group``  : N_market / N_tech / N_customer                       → 真实 `form_coop_group`(BCS REST,本地 double)→ session 轮询 → completed→PASS。
  - ``bbs``         : N_field_interview                                   → MISS@MAX_DEPTH=1 → `miss_depth_exhausted` → 节点 HUNG+升 BBS(bbs_mode=true,
                       根保 PLANNING 可恢复态) → 风清扬 bbs-relay-pickup 自驱 claim→attach→report root_verified=true → 根 DONE / 图 DONE。

关键架构前提(spec §10.5 可恢复态 + v5 guard):
  - `miss_depth_exhausted` 升 BBS 时,若有 RUNNING 兄弟,不置根 HUNG(可恢复);owner 也不重 plan(避免抢占 BBS 接力)。
  - BBS 中继是唯一从 bbs_mode 收口到 DONE 的路径(``on_bbs_report root_verified=true`` 翻 PLANNING→DONE)。
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
_TIMEOUT = float(os.environ.get("SINGLEBOX_TASK_E2E_TIMEOUT", "2000"))

SKILLS_DIR = Path(__file__).parent / "skills"
# bbs-relay-pickup skill 落在 spec 目录(spec 仓;非 src/backend/skills)。
_BBS_SKILL = str(
    Path(__file__).resolve().parents[5]
    / "specs" / "2026-08-09-task-goal-driven-task-runner-bbs" / "bbs-relay-pickup"
)

# 每次进程随机 task_id(避免内存图跨次重跑 task_id 重复)。
TASK_ID = f"t_case_{uuid.uuid4().hex[:6]}"

# 角色 bot:与 search skill 确定式映射**完全对名**(catalog 按 bot_name 匹配真实 bot_id)。
ROLE_BOTS = {
    "N_overview": "行业信息抓取Bot",          # single_bot
    "N_market_a": "市场需求分析Bot",          # coop_group(manager)
    "N_market_b": "资本市场投资Bot",          # coop_group(worker)
    "N_tech_a": "数据中心存储架构师",          # coop_group(manager)
    "N_tech_b": "企业级SSD专家",              # coop_group(worker)
    "N_compete": "存储行业供应链专家",         # single_bot
    "N_customer_a": "ToG方案专家",            # coop_group(manager)
    "N_customer_b": "ToB方案专家",            # coop_group(worker)
    "N_customer_c": "采购决策专家",            # coop_group(worker)
    "N_practice_bbs": "实践bbs专家Bot",        # single_bot
    "N_report": "报告聚合Bot",                 # single_bot
}
OWNER_BOT_NAME = "task-owner-bot"
RELAY_BOT_NAME = "风清扬-relay"

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}


def _execute_body(owner_id: str) -> dict:
    """``POST /openapi/v1/collaboration/tasks/execute`` 请求体(存储行业尽调案例)。MAX_DEPTH=1 → N_field_interview MISS@MAX 升 BBS。"""
    return {
        "task_spec": {
            "metadata": {
                "task_id": TASK_ID,
                "title": "存储行业尽调",
                "instruction": (
                    "AI基础设施驱动下,企业级与数据中心存储行业的最新变化、竞争格局与进入机会,"
                    "产出一份尽调报告。"
                ),
            },
            "context": {"background": "存储行业尽调", "extend_props": {}},
            "goal": {
                "objective": "产出一份存储行业尽调报告",
                "acceptances": [
                    {"id": "ac1", "description": "明确存储行业当前是否具备中短期投资价值"},
                    {"id": "ac2", "description": "明确最值得跟踪的细分赛道、公司类型和核心变量"},
                    {"id": "ac3", "description": "提供市场规模、竞争格局、技术演进、客户需求四大维度的系统分析"},
                    {"id": "ac4", "description": "至少形成 5 条核心投资判断"},
                    {"id": "ac5", "description": "每条投资判断需同时说明支持证据、风险因素、需要进一步验证的问题"},
                ],
            },
        },
        "source_type": "bot",
        "owner_bot_id": owner_id,
        # MAX_DEPTH=1:N_field_interview MISS 即在 <root 子> 下 @depth=1 → miss_depth_exhausted 触发升 BBS。
        # BBS_MAX_DEPTH=3 留 relay 接力预算。
        "execution_config": {"MAX_DEPTH": 1, "BBS_MAX_DEPTH": 3},
    }


def _wake_prompt() -> str:
    """唤醒 relay bot 自驱 bbs-relay-pickup(用例只唤醒不代调 bbs/* 路由)。"""
    return (
        "请执行 bbs-relay-pickup skill 完成 BBS 自主接力。\n"
        f"任务 task_id={TASK_ID} 已自然升 BBS(bbs_mode=true、根 PLANNING、图空闲),"
        "等待你自主接力收口。\n"
        f"task API backend base url: {_BACKEND}(用 exec+curl 直调,"
        f"例 curl {_BACKEND}/openapi/v1/collaboration/tasks/list --json ...)。\n"
        "按 bbs-relay-pickup SKILL.md 6 步自驱:\n"
        "  步① GET /openapi/v1/collaboration/tasks/list 枚举 task_id,逐个 GET /openapi/v1/collaboration/tasks/dashboard 并筛 extend_props.bbs_mode==true;\n"
        "  步② POST /api/v1/collaboration/tasks/bbs/claim 占根;\n"
        "  步③ 读根 goal + 已 DONE 叶子 + 前序 scoped 节点 checkpoint 自判 full/partial/skip;\n"
        "  步④ POST /api/v1/collaboration/tasks/bbs/attach 挂一个 run_mode=bbs 节点 + 用你自身能力执行该节点指令;\n"
        "  步⑤ POST /api/v1/collaboration/tasks/bbs/result 写回(verdict=PASS、acceptances_metric 列出达成的 AC、"
        "gaps=[]、带 output_patch={完整产出}、root_verified=true 收口全图)→ claim 自动释放。\n"
        "本案例自判 full:剩余尽调目标你一次唤醒做满 → root_verified=true 收口全图 DONE。"
    )


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox e2e")
class TestTaskIntegrationE2E(unittest.TestCase):
    def test_plan_decompose_dispatch_execute_report(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) provisioning(幂等建 bot + 装 skill):
        #    - owner bot:planning + search(规划/拍板)
        #    - 11 角色 bot:acceptance(skill 用于叶子执行时自验收,但 singlebox 经 owner dispatch + poller 收 PASS,
        #      角色 bot 执行产出会经 BcsSessionTranslator 解析为 PASS,acceptance 主要作上下文)
        #    - relay bot:acceptance + bbs-relay-pickup(自驱 BBS 接力)
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        owner_id = await prov.create_bot(bot_name=OWNER_BOT_NAME)
        await prov.install_skills(
            owner_id, [str(SKILLS_DIR / "planning"), str(SKILLS_DIR / "search")]
        )
        role_ids = {nid: await prov.create_bot(bot_name=name) for nid, name in ROLE_BOTS.items()}
        _acceptance = str(SKILLS_DIR / "acceptance")
        for nid, rid in role_ids.items():
            await prov.install_skills(rid, [_acceptance])
            print(f"[provision] role {nid}({rid}) ← acceptance")
        relay_id = await prov.create_bot(bot_name=RELAY_BOT_NAME)
        await prov.install_skills(relay_id, [_acceptance, _BBS_SKILL])
        await prov._aclose()
        print(f"[provision] owner={owner_id} roles={role_ids} relay={relay_id}")

        # live adapter:唤醒 relay bot 自驱 bbs-relay-pickup
        adapter = SingleboxEngineAdapter(backend_base_url=_BACKEND, user_id=_USER_ID)
        self.addCleanup(lambda: loop.run_until_complete(adapter._aclose()))

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 2) POST /openapi/v1/collaboration/tasks/execute → backend 进程内真实 engine 后台推进首帧
            r = await cli.post(f"{_BACKEND}/openapi/v1/collaboration/tasks/execute", json=_execute_body(owner_id))
            r.raise_for_status()
            print(f"[execute] {r.json().get('message')} data={r.json().get('data')}")

            # 3) 轮询至自然升 BBS(bbs_mode=true)或 DONE 或超时
            g: dict = {}
            deadline = time.monotonic() + _TIMEOUT
            bbs_mode_seen = False
            # 4) 已升 BBS → 唤醒 relay bot 自驱接力;接力未收口且图空闲可再唤醒,上限 BBS_MAX_DEPTH 次
            wakes = 0
            _MAX_WAKES = 3
            while time.monotonic() < deadline:
                r = await cli.get(f"{_BACKEND}/openapi/v1/collaboration/tasks/dashboard", params={"task_id": TASK_ID})
                r.raise_for_status()
                g = r.json().get("data") or {}
                ep = g.get("extend_props") or {}
                snap = [
                    (t.get("node_id"), t.get("status"),
                     (t.get("run_info") or {}).get("run_mode") or "",
                     str((t.get("run_info") or {}).get("assignee") or "")[:24])
                    for t in g.get("tasks") or []
                ]
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} "
                      f"bbs_mode={ep.get('bbs_mode')} nodes={snap}")

                if g.get("status") in ("DONE", "HUNG"):
                    break

                # 升 BBS 且图空闲(无 RUNNING + 根 PLANNING + 未占)→ 唤醒 relay
                if ep.get("bbs_mode"):
                    bbs_mode_seen = True
                    busy = any((t.get("status") == "RUNNING") for t in g.get("tasks") or [])
                    held = ep.get("bbs_owner")
                    if (not busy and not held) or wakes == 0:
                        if wakes < _MAX_WAKES:
                            wakes += 1
                            print(f"[wake#{wakes}] 唤醒 relay bot 自驱 bbs-relay-pickup ...")
                            try:
                                run = await adapter.send_and_wait_async(
                                    bot_id=relay_id, message=_wake_prompt(), timeout=600.0
                                )
                                status = run.get("status")
                                content = (run.get("result") or {}).get("content") or ""
                                print(f"[wake#{wakes}] status={status} content[:300]={content[:300]!r}")
                            except Exception as exc:  # noqa: BLE001
                                print(f"[wake#{wakes}] adapter 异常:{exc!r}")
                await asyncio.sleep(8.0)

        # 5) 断言:全图闭环 DONE + 三种执行模态全覆盖
        print(f"[final] graph={g.get('status')} tasks={len(g.get('tasks') or [])}")
        for t in g.get("tasks") or []:
            ri = t.get("run_info") or {}
            print(f"  - {t.get('node_id'):28} {t.get('status'):8} "
                  f"mode={ri.get('run_mode') or '-':11} assignee={str(ri.get('assignee') or '-')[:24]}")

        self.assertEqual(g.get("status"), "DONE", f"全图未闭环 DONE:status={g.get('status')}")
        nodes = {t["node_id"]: t for t in g.get("tasks") or []}

        # 根 DONE
        self.assertIn(TASK_ID, nodes, "根 task 未出现")
        self.assertEqual(nodes[TASK_ID]["status"], "DONE", "根未 DONE")

        # 模态 1:single_bot(N_overview 由真实角色 bot 执行)
        ov = nodes.get("N_overview")
        self.assertIsNotNone(ov, "N_overview 未被 plan 出")
        self.assertEqual(ov["status"], "DONE", "N_overview 未 DONE")
        self.assertEqual((ov["run_info"] or {}).get("run_mode"), "single_bot", "N_overview 非 single_bot")
        self.assertEqual((ov["run_info"] or {}).get("assignee"), role_ids["N_overview"],
                         "N_overview 未命中真实角色 bot 行业信息抓取Bot")

        # 模态 2:coop_group(N_market/N_tech/N_customer 三协作群)
        for gid in ("N_market", "N_tech", "N_customer"):
            nd = nodes.get(gid)
            self.assertIsNotNone(nd, f"{gid} 未出现")
            self.assertEqual(nd["status"], "DONE", f"{gid} 未 DONE")
            self.assertEqual((nd["run_info"] or {}).get("run_mode"), "coop_group", f"{gid} 非 coop_group")
            # 群 id 前缀容忍两种后端:本地 stub/double 产 ``grp_<8hex>``;真 BCS(:21000)产 ``bcs_grp_<uuid>``。
            self.assertTrue(str((nd["run_info"] or {}).get("assignee") or "").startswith(("grp_", "bcs_grp_")),
                            f"{gid} assignee 非群 id")
        # 实际建了 3 个群
        group_count = sum(
            1 for n in g.get("tasks") or []
            if (n.get("run_info") or {}).get("run_mode") == "coop_group" and n.get("status") == "DONE"
        )
        self.assertEqual(group_count, 3, f"协作群数量 != 3(实际 {group_count})")

        # 模仿 BBS 接力覆盖:N_field_interview MISS→HUNG→升 BBS,relay attach 一个 run_mode=bbs scoped 节点 收口
        nm = nodes.get("N_field_interview")
        self.assertIsNotNone(nm, "N_field_interview 未出现(BBS 升级点缺失)")
        self.assertEqual(nm["status"], "HUNG", "N_field_interview 未 HUNG(应 MISS@MAX→HUNG 升 BBS)")
        self.assertTrue((g.get("extend_props") or {}).get("bbs_mode"), "图未置 bbs_mode(BBS 未升)")
        self.assertTrue(bbs_mode_seen, "bbs_mode 未在轮询中观察到")
        bbs_scoped = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "bbs" and t["node_id"] != TASK_ID
        ]
        self.assertGreaterEqual(len(bbs_scoped), 1, "无 relay 自驱的 bbs scoped 节点")
        for n in bbs_scoped:
            self.assertEqual(n.get("status"), "DONE", f"scoped 未 DONE:{n.get('node_id')}")
            self.assertEqual((n.get("run_info") or {}).get("assignee"), relay_id,
                             f"scoped 非 relay 接力:{n.get('node_id')}")


if __name__ == "__main__":
    unittest.main()
