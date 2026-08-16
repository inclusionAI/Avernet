"""任务目标驱动执行框架 — singlebox 真实端到端集成用例(统一一个用例)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地 ``./scripts/singlebox.sh start all`` 起好 singlebox 后:

  SINGLEBOX_TASK_E2E=1 \
    .venv/bin/python -m pytest tests/community/core/task/singlebox_e2e/test_task_integration_e2e.py -s

机制统一:**provisioning(幂等建 bot + 装 skill)→ POST /api/task/execute(HTTP facade)→ GET /api/task/dashboard 轮询**。
经 backend 进程内 DI(TaskService → ExecutionEngine → 真实 adapter/double BCS/in-process discover)真实推进,
非测试进程内直调引擎。框架零 case 知识;不同链路覆盖由"建几个 bot + 用什么需求"决定,用例机制不变:

- ``HIT_SINGLE`` 命中的角色 bot → 真实 execute(poller 回投 COMPLETED→PASS)。
- ``HIT_MULTI_BOTS`` 协作群 → singlebox 无真实 BCS,backend 进程内走 ``_DoubleBcsClient`` 模拟回投(真实协作群留 corp)。
- 角色 bot 名 == search skill 剧本角色名 → skill 在 catalog 里按名匹配出真实 bot_id(契约见 search/SKILL.md)。

单 box 启用真实 engine 由 ``DEPLOY_PROFILE=singlebox`` 驱动(task_module DI),``singlebox.sh start all`` 自带,零额外 env。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
import uuid
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_runner.integration.singlebox_engine_adapter import (
    SingleboxBotProvisioner,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "146836")
_TIMEOUT = float(os.environ.get("SINGLEBOX_TASK_E2E_TIMEOUT", "1500"))

SKILLS_DIR = Path(__file__).parent / "skills"
# 每次进程随机:避免后端 in-mem TaskGraphService 跨次重跑时 task_id 重复 → GraphAlreadyInitializedError。
TASK_ID = f"t_case_{uuid.uuid4().hex[:6]}"

# search skill 剧本角色名(HIT_SINGLE 真实叶节点)→ bot_name;协作群 bot 走 BCS double 不建。
ROLE_BOTS = {
    "N_overview": "行业信息尽调Bot",
    "N_marketing_analyst": "市场规模维度分析Bot",
    "N_technology_analyst": "技术演进维度分析Bot",
    "N_competition_analyst": "竞争格局维度分析Bot",
    "N_customer_genai": "客户需求维度-GenAI场景分析Bot",
    "N_customer_rag": "客户需求维度-RAG场景分析Bot",
    "N_customer_green": "客户需求维度-绿色数据中心分析Bot",
    "N_customer_fabric": "客户需求维度-Fabric高可靠场景分析Bot",
    "N_to_b_analyst": "ToB解决方案专家Bot",
    "N_to_g_analyst": "ToG解决方案专家",
    "N_bbs_analyst": "综合分析Bot",
}
OWNER_BOT_NAME = "task-owner-bot"

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}


def _execute_body(owner_id: str) -> dict:
    """``POST /api/task/execute`` 请求体(TaskInfoDTO;案例 gwqie46v7hzr1w6h 存储行业尽调)。"""
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
        "source_channel_type": "bot",
        "source_channel_id": owner_id,
        "execution_config": {"MAX_DEPTH": 3, "BBS_MAX_DEPTH": 3},
    }


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox e2e")
class TestTaskIntegrationE2E(unittest.TestCase):
    def test_plan_decompose_dispatch_execute_report(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) provisioning(owner bot 装 planning+search skill + 4 角色 bot;幂等)
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        owner_id = await prov.create_bot(bot_name=OWNER_BOT_NAME)
        await prov.install_skills(
            owner_id, [str(SKILLS_DIR / "planning"), str(SKILLS_DIR / "search")]
        )
        role_ids = {nid: await prov.create_bot(bot_name=name) for nid, name in ROLE_BOTS.items()}
        # 角色 bot(真实叶节点 worker)装 acceptance skill:叶子 execute 时 worker 自调自验收(方案 Y)
        _acceptance = str(SKILLS_DIR / "acceptance")
        for nid, rid in role_ids.items():
            await prov.install_skills(rid, [_acceptance])
            print(f"[provision] role {nid}({rid}) ← acceptance")
        await prov._aclose()
        print(f"[provision] owner={owner_id} roles={role_ids}")

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 2) POST /api/task/execute → backend 进程内真实 engine 推进首帧
            r = await cli.post(f"{_BACKEND}/api/task/execute", json=_execute_body(owner_id))
            r.raise_for_status()
            print(f"[execute] {r.json().get('message')} data={r.json().get('data')}")

            # 3) GET /api/task/dashboard 轮询,直到全图 DONE / HUNG / 超时
            deadline = time.monotonic() + _TIMEOUT
            g: dict = {}
            while time.monotonic() < deadline:
                r = await cli.get(f"{_BACKEND}/api/task/dashboard", params={"task_id": TASK_ID})
                r.raise_for_status()
                g = r.json().get("data") or {}
                snap = [
                    (t.get("node_id"), t.get("status"),
                     (t.get("run_info") or {}).get("run_mode") or "",
                     str((t.get("run_info") or {}).get("assignee") or "")[:24])
                    for t in g.get("tasks") or []
                ]
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} nodes={snap}")
                if g.get("status") in ("DONE", "HUNG"):
                    break
                await asyncio.sleep(5.0)

        # 4) 断言:全图闭环 + HIT_SINGLE 真实角色 bot + 协作群 double
        print(f"[final] graph={g.get('status')} tasks={len(g.get('tasks') or [])}")
        for t in g.get("tasks") or []:
            ri = t.get("run_info") or {}
            print(f"  - {t.get('node_id'):18} {t.get('status'):8} "
                  f"mode={ri.get('run_mode') or '-':11} assignee={ri.get('assignee') or '-'}")

        self.assertEqual(g.get("status"), "DONE", f"全图未闭环 DONE:status={g.get('status')}")
        nodes = {t["node_id"]: t for t in g.get("tasks") or []}
        self.assertIn(TASK_ID, nodes, "根 t_case 未出现")
        self.assertEqual(nodes[TASK_ID]["status"], "DONE")
        ov = nodes.get("N_overview")
        self.assertIsNotNone(ov, "N_overview 未被 plan 出")
        self.assertEqual(ov["status"], "DONE")
        self.assertEqual((ov["run_info"] or {}).get("run_mode"), "single_bot")
        self.assertEqual((ov["run_info"] or {}).get("assignee"), role_ids["N_overview"],
                         "N_overview 未命中真实角色 bot 行业信息抓取Bot")
        for gid in ("N_market", "N_tech", "N_customer"):
            nd = nodes.get(gid)
            self.assertIsNotNone(nd, f"{gid} 未出现")
            self.assertEqual((nd["run_info"] or {}).get("run_mode"), "coop_group", f"{gid} 非协作群模态")
            self.assertTrue(str((nd["run_info"] or {}).get("assignee") or "").startswith("grp_"),
                            f"{gid} assignee 非群 id")


if __name__ == "__main__":
    unittest.main()
