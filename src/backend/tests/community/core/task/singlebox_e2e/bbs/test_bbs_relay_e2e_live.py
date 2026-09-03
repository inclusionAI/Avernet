"""BBS 自主接力 live e2e(金庸案例)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地 ``./scripts/singlebox.sh start all`` 起好 singlebox 后:

  SINGLEBOX_TASK_E2E=1 \
    src/backend/.venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e_live.py -s

# 场景(对齐 spec §6 / test_task_integration_e2e.py)

- 新建 ``task-owner-bot``(装 planning+search,做任务规划/分解)+「金庸」(装 ``arch-analysis`` +
  ``bbs-relay-pickup``,BBS 接力执行者)。
- 任务目标:**整理支付宝公司内部技术架构师**。理论上:owner 规划分解后,子任务"找架构师"经普通搜索
  找不到合适的人 → 升 BBS;只有「金庸」会通过 bbs 接力把各方向的架构师逐段整理出来,最终满足根目标 → 图 SUCCESS。

# 为什么是 in-process 图 + live adapter(设计说明)

- BBS 接力的写口(``bbs/claim``/``attach``/``result``)+ ``on_bbs_report`` 都是 collector-free 的图操作,
  不跑框架 dispatch/planner,因此用一个 in-process FastAPI+Injector(stub discover,同
  ``test_bbs_claim_route.py`` 套路)承载图与路由即可,接力 mechanics 无需 singlebox 全栈。
- 金庸的 ``arch-analysis`` 是真实 LLM 推理,经 ``SingleboxEngineAdapter``(直连 singlebox 上金庸的
  引擎 ws)live 调用;金庸本体由 ``SingleboxBotProvisioner`` 真实建 bot + 装 skill。
- "单 bot 找不到 → 升 BBS"在框架里要求任务处于 **bbs 可恢复态**(根 ``PLANNING`` + ``bbs_mode=True``;
  见 spec §10.5 seam:图级 ``HUNG`` 是硬终态,BBS 不碰)。自然升 BBS 对非案例目标易落到图级 ``HUNG``
  (planning skill 是案例剧本式),故本用例**直接构造 bbs 可恢复态**来演练金庸的接力,模拟
  "普通派发找不到架构师后,任务被升到 BBS 且仍可恢复"。
- `task-owner-bot` 作任务的 planner/source(对齐范本 provisioning)。自然链里 owner 的 planning skill
  是案例剧本式(只认存储行业 case),且 §10.5 seam 下"整任务单 bot 做不了"会同步图级 ``HUNG``(BBS 接不上);
  故本用例 **provision owner 但接力态由用例构造**为可恢复态,金庸再 self-decompose via attach 接力。
  若要让 owner 真实规划 + 自然升 BBS 接力成功,需另做 planning skill 剧本并(可能)放宽 §10.5 seam——独立工作,不在本用例。
- 接力循环由本用例编排(充当"极薄外部触发器 + bbs-relay-pickup 的 loop 驱动"),每段:
  claim → attach(scoped 子节点:某方向找架构师)→ 金庸 arch-analysis(live)→ result(PASS+checkpoint)
  → 释放 → 下段;根收口由框架经 owner 复核自判(非 bot 声明,无 root_verified;见 natual live 测)。复刻 bbs-relay-pickup SKILL.md 的 6 步 loop。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import unittest
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi_injector import attach_injector
from fastapi.testclient import TestClient
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.openapi_v1.task.router import router as task_router
from agentclaw.community.adapters.http.task.router import router as task_internal_router
from agentclaw.community.api.bot_discover_service import BotDiscoverServiceProtocol
from agentclaw.community.api.bot_public_service import BotPublicServiceProtocol
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    Status,
    TaskGraphPatch,
    TaskInfo,
    TaskSpec,
)
from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService
from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    SingleboxBotProvisioner,
    SingleboxEngineAdapter,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "146836")
_OWNER_BOT_NAME = "task-owner-bot"  # 对齐 test_task_integration_e2e.py:owner 做 planning+search
_JY_BOT_NAME = "金庸"

SKILLS_DIR = Path(__file__).parent / "../skills"
_ARCH_SKILL = str(SKILLS_DIR / "arch-analysis")
# bbs-relay-pickup skill 落在 spec 目录下(非 src/backend/skills);
# test 文件在 <repo>/src/backend/tests/community/core/task/singlebox_e2e/ ,parents[5] = <repo>/src/backend
_BBS_SKILL = str(
    Path(__file__).resolve().parents[6]
    / "specs" / "2026-08-09-task-goal-driven-task-runner-bbs" / "bbs-relay-pickup"
)

# 任务目标:整理支付宝公司内部技术架构师。金庸按方向拆段接力。
TASK_ID = f"t_arch_{uuid.uuid4().hex[:6]}"
SUB_DOMAINS = ["基础架构", "中间件架构", "数据架构", "业务架构"]


def _task_info(owner_id: str) -> TaskInfo:
    return TaskInfo(
        task_spec=TaskSpec(
            metadata=Metadata(
                task_id=TASK_ID,
                title="整理支付宝公司内部技术架构师",
                instruction=(
                    "整理支付宝(蚂蚁集团)内部各技术架构方向的架构师/负责人清单,"
                    "覆盖基础架构/中间件/数据/业务等方向。"
                ),
            ),
            context=Context(background="支付宝内部技术架构师梳理", extend_props={}),
            goal=Goal(
                objective="整理支付宝公司内部技术架构师(各方向架构师姓名/角色/职责清单)",
                acceptances=[
                    AcceptanceCriteria(id="ac1", description="覆盖基础架构/中间件/数据/业务等主要技术架构方向"),
                    AcceptanceCriteria(id="ac2", description="每个方向给出架构师清单(姓名或角色 + 职责)"),
                ],
            ),
        ),
        source_type="bot",
        owner_bot_id=owner_id,
        execution_config={"MAX_DEPTH": 2, "BBS_MAX_DEPTH": len(SUB_DOMAINS) + 2},
    )


class _StubDiscoverModule(Module):
    """stub discover/bot_public:接力 mechanics 不走 dispatch/search,返空即可装配。"""

    @singleton
    @provider
    def discover(self) -> BotDiscoverServiceProtocol:
        class _D:
            def search_by_keyword(self, **kw):
                return {"total": 0, "items": []}

        return _D()  # type: ignore[return-value]

    @singleton
    @provider
    def bot_public(self) -> BotPublicServiceProtocol:
        class _B:
            def search_public_bots_by_keyword(self, **kw):
                return {"total": 0, "items": []}

        return _B()  # type: ignore[return-value]


def _build_client() -> tuple[TestClient, Injector]:
    from agentclaw.community.di.modules.task_module import TaskModule

    injector = Injector([TaskModule(), _StubDiscoverModule()])
    app = FastAPI()
    app.include_router(task_router)
    app.include_router(task_internal_router)
    attach_injector(app, injector)
    return TestClient(app), injector


def _seed_bbs_recoverable(injector: Injector, owner_id: str) -> None:
    """构造 bbs 可恢复态:建图 → 置 bbs_mode=True → 根 PLANNING。

    模拟"普通派发找不到架构师 → 任务被升 BBS 且仍可恢复(根 PLANNING,非图级 HUNG)"。
    PENDING→PLANNING 不在 _DIRECT_TRANSITIONS,故根 PLANNING 用白盒直置
    (query_task_dashboard 返回的是 self._graphs 的 live 引用,同 T6 test ruling)。
    """
    graph_svc = injector.get(TaskGraphService)
    graph_svc.initialize_graph(_task_info(owner_id))
    graph_svc.update_task_graph_info(TASK_ID, TaskGraphPatch(extend_props_patch={"bbs_mode": True}))
    graph = graph_svc.query_task_dashboard(TASK_ID)
    root = next(n for n in graph.tasks if n.node_id == TASK_ID)
    root.status = Status.PLANNING


def _parse_architects(text: str) -> dict | None:
    """从金庸 arch-analysis 回的文本里抠 ```json 代码块;抠不到返 None。"""
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox live e2e")
class TestBbsRelayE2ELive(unittest.TestCase):
    def test_feng_qingyang_relays_architects_via_bbs(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    async def _run(self) -> None:
        # 1) provisioning(对齐 test_task_integration_e2e.py):
        #    task-owner-bot 做 planning+search(owner 规划/分解任务);
        #    金庸 装 arch-analysis + bbs-relay-pickup(BBS 接力执行者)。幂等。
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        owner_id = await prov.create_bot(bot_name=_OWNER_BOT_NAME)
        await prov.install_skills(
            owner_id, [str(SKILLS_DIR / "planning"), str(SKILLS_DIR / "search")]
        )
        jy_id = await prov.create_bot(bot_name=_JY_BOT_NAME)
        await prov.install_skills(jy_id, [_ARCH_SKILL, _BBS_SKILL])
        await prov._aclose()
        print(f"[provision] owner={owner_id} ← planning+search ; 金庸={jy_id} ← arch-analysis+bbs-relay-pickup")

        # 2) in-process 图 + 路由(接力 mechanics);构造 bbs 可恢复态(source = owner)
        client, injector = _build_client()
        _seed_bbs_recoverable(injector, owner_id=owner_id)
        print(f"[seed] task={TASK_ID} source=owner({owner_id}) bbs_mode=True root=PLANNING(可恢复)")

        # 3) live adapter:金庸的 arch-analysis 真实推理
        adapter = SingleboxEngineAdapter(backend_base_url=_BACKEND, user_id=_USER_ID)

        def _dash() -> dict:
            return client.get("/openapi/v1/collaboration/tasks/dashboard", params={"task_id": TASK_ID}).json()["data"]

        def _node(g: dict, nid: str) -> dict | None:
            return next((n for n in g["tasks"] if n["node_id"] == nid), None)

        total_architects = 0
        try:
            # 4) BBS 接力 loop(本用例编排 = 极薄触发器 + bbs-relay-pickup loop):每方向一段
            for i, sub in enumerate(SUB_DOMAINS):

                # 步② CAS 占根(同 bot 幂等;前段 result 已释放 claim,这里重新 claim)
                r = client.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": TASK_ID, "bot_id": jy_id})
                self.assertEqual(r.status_code, 200, f"claim 未成功(@{sub}):{r.text}")
                # 接单确认:claim 后根 bbs_owner == 金庸
                g = _dash()
                root = _node(g, TASK_ID)
                self.assertEqual(
                    (root["run_info"]["extend_props"] or {}).get("bbs_owner"), jy_id,
                    f"claim 后根 bbs_owner 非金庸(@{sub})",
                )
                # CAS 排他确认:owner 再 claim 同任务应 409(仅首段验一次)
                if i == 0:
                    r2 = client.post("/api/v1/collaboration/tasks/bbs/claim", json={"task_id": TASK_ID, "bot_id": owner_id})
                    self.assertEqual(r2.status_code, 409, f"第二 bot claim 未被 CAS 拒(应 409):{r2.text}")
                    print(f"[relay@{sub}] claim 200 bbs_owner=金庸 (CAS: owner→409 ✓)")
                else:
                    print(f"[relay@{sub}] claim 200 bbs_owner=金庸")

                # 步④ 挂一个 scoped 节点(该方向找架构师)+ start
                r = client.post(
                    "/api/v1/collaboration/tasks/bbs/attach",
                    json={
                        "task_id": TASK_ID,
                        "parent_node_id": TASK_ID,  # 根
                        "bot_id": jy_id,
                        "task_spec": {
                            "metadata": {
                                "task_id": f"{TASK_ID}::bbs::{sub}",
                                "title": f"整理支付宝内部「{sub}」技术架构师",
                                "instruction": f"用 arch-analysis 分析支付宝内部「{sub}」方向的技术架构师,产出清单。",
                            },
                            "context": {"background": "支付宝内部技术架构师梳理", "extend_props": {}},
                            "goal": {
                                "objective": f"列出支付宝内部「{sub}」方向的技术架构师清单",
                                "acceptances": [
                                    {"id": "ac1", "description": "给出该方向架构师姓名/角色 + 职责"}
                                ],
                            },
                        },
                    },
                )
                self.assertEqual(r.status_code, 200, f"attach 未成功(@{sub}):{r.text}")
                node_id = r.json()["data"]["node_id"]
                self.assertTrue(node_id.startswith("bbs-"), f"node_id 非 bbs- 前缀:{node_id}")
                # 执行确认:attach 后节点 RUNNING / run_mode=bbs / assignee=金庸
                g = _dash()
                nd = _node(g, node_id)
                self.assertIsNotNone(nd, f"attach 后未找到节点 {node_id}(@{sub})")
                self.assertEqual(nd["status"], "RUNNING", f"attach 后节点非 RUNNING(@{sub}):{nd['status']}")
                self.assertEqual((nd["run_info"] or {}).get("run_mode"), "bbs", f"节点非 run_mode=bbs(@{sub})")
                self.assertEqual((nd["run_info"] or {}).get("assignee"), jy_id, f"节点 assignee 非金庸(@{sub})")
                print(f"[relay@{sub}] attach 200 node={node_id} RUNNING/bbs/金庸 ✓")

                # 步④ 执行:金庸 arch-analysis(live LLM)识别该方向架构师
                prompt = (
                    f"请用 arch-analysis skill 分析:支付宝(蚂蚁集团)内部「{sub}」方向的技术架构师是谁?"
                    f"给出该方向架构师清单(姓名/角色/职责)。按 arch-analysis SKILL.md 的 ```json 格式输出。"
                )
                finding_text = ""
                try:
                    run = await adapter.send_and_wait_async(
                        bot_id=jy_id, message=prompt, timeout=180.0
                    )
                    # bot 文本在 run["result"]["content"](adapter _ws_chat_roundtrip 终态返此结构)
                    status = run.get("status")
                    content = (run.get("result") or {}).get("content") or ""
                    if status != "COMPLETED" or not content:
                        finding_text = f"<arch-analysis status={status} error={run.get('error')}>"
                        print(f"[relay@{sub}] arch-analysis 未拿到内容:{finding_text}")
                    else:
                        parsed = _parse_architects(content)
                        finding_text = json.dumps(parsed, ensure_ascii=False) if parsed else content
                        n_arch = len((parsed or {}).get("architects", [])) if parsed else 0
                        total_architects += n_arch
                        print(f"[relay@{sub}] arch-analysis status={status} parsed={n_arch} "
                              f"content[:200]={content[:200]!r}")
                except Exception as exc:  # noqa: BLE001  # live LLM 失败不阻断接力 mechanics 验证
                    finding_text = f"<arch-analysis unavailable: {exc!r}>"
                    print(f"[relay@{sub}] arch-analysis 异常:{exc!r}")

                # 步⑤ 回投:PASS + checkpoint;根收口由框架经 owner 复核自判(非 bot 声明,无 root_verified)
                r = client.post(
                    "/api/v1/collaboration/tasks/bbs/result",
                    json={
                        "task_id": TASK_ID,
                        "node_id": node_id,
                        "bot_id": jy_id,
                        "acceptance_result": {
                            "verdict": "DONE",
                            "acceptances_metric": [],
                            "gaps": [],
                        },
                        "output_patch": {"domain": sub, "architects": finding_text},
                    },
                )
                self.assertEqual(r.status_code, 200, f"result 未成功(@{sub}):{r.text}")
                # 上报确认:节点 SUCCESS;bbs_owner 已释放。根是否 SUCCESS 由框架复核根 gap 自判;
                # 本 in-process 编排无 owner bot→根不收口(不断言根 DONE,根收口见 natual live 测)。
                g = _dash()
                nd = _node(g, node_id)
                self.assertEqual(nd["status"], "SUCCESS", f"result 后节点非 SUCCESS(@{sub}):{nd['status']}")
                root = _node(g, TASK_ID)
                self.assertIsNone(
                    (root["run_info"]["extend_props"] or {}).get("bbs_owner"),
                    f"result 后 bbs_owner 未释放(@{sub})",
                )
                print(f"[relay@{sub}] result 200 node=SUCCESS root={root['status']} bbs_owner=释放 ✓")
        finally:
            try:
                await adapter._aclose()
            except Exception:
                pass

        # 5) 断言:各方向 scoped bbs 节点 SUCCESS(assignee=金庸,带架构师 checkpoint)+ bbs_mode 已置。
        # 根/图是否 DONE 由框架复核根 gap 自判(无 root_verified);本 in-process 编排无 owner bot→不收口,
        # 故不断言图 SUCCESS(根收口见 natual live 测 ``test_bbs_relay_e2e_natual``)。
        g = client.get("/openapi/v1/collaboration/tasks/dashboard", params={"task_id": TASK_ID}).json()["data"]
        self.assertTrue(g["extend_props"].get("bbs_mode"), "图未置 bbs_mode")
        self.assertGreater(
            total_architects, 0,
            "arch-analysis 全程未产出任何架构师(检查 skill 是否激活 + 输出是否 ```json 格式);"
            "看上方各段 content[:200] 日志定位",
        )

        bbs_nodes = [
            t for t in g["tasks"]
            if (t.get("run_info") or {}).get("run_mode") == "bbs" and t["node_id"] != TASK_ID
        ]
        self.assertEqual(
            len(bbs_nodes), len(SUB_DOMAINS),
            f"bbs scoped 节点数 != 方向数:{[n['node_id'] for n in bbs_nodes]}",
        )
        for n in bbs_nodes:
            ri = n["run_info"] or {}
            self.assertEqual(n["status"], "SUCCESS", f"scoped 未 SUCCESS:{n['node_id']}")
            self.assertEqual(ri.get("assignee"), jy_id, f"scoped 非 金庸 接力:{n['node_id']}")
            self.assertTrue(
                (ri.get("output") or {}).get("architects"),
                f"scoped 缺架构师 checkpoint:{n['node_id']}",
            )
        print(f"[final] graph={g['status']} 金庸接力段={len(bbs_nodes)} 架构师(parsed)={total_architects}")


if __name__ == "__main__":
    unittest.main()
