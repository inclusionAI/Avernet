"""BBS 自然升 BBS + 自主接力 live e2e(风清扬案例)。

gated by ``SINGLEBOX_TASK_E2E=1``。本地 ``./scripts/singlebox.sh start all`` 起好 singlebox 后:

  SINGLEBOX_TASK_E2E=1 \
    src/backend/.venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/test_bbs_relay_e2e_natual.py -s

# 场景(对齐 spec §6.1 Scenario A / test_task_integration_e2e.py)

- owner bot(装 ``planning-arch`` + ``search``,通用规划/分解)+「风清扬」(装 ``arch-analysis`` +
  ``bbs-relay-pickup``,BBS 接力执行者)。
- 任务目标:**整理支付宝公司内部「基础架构」方向技术架构师**。自然链:owner 用通用 planning-arch 规划分解
  → 子任务"找架构师"经 search 派发 → 候选 bot 都不匹配 → ``on_miss@MAX_DEPTH`` 自然升 BBS
  (``bbs_mode=True``、根 ``PLANNING``、图空闲,即 spec §10.5 可恢复态)。
- 升 BBS 后本用例**唤醒一次风清扬**:`bbs-relay-pickup` 由风清扬自驱 6 步(发现 → claim → 自判 →
  attach → 用自身能力执行 → ``bbs/result`` 写回收口),不由用例代调 ``bbs/*`` 路由。
- 风清扬自判 ``full`` → 一段接力做满剩余 → ``root_verified=true`` → 根 ``DONE`` → 图 ``DONE``。

# 与 ``test_bbs_relay_e2e_live.py`` 的区别(live 是演练接力 mechanics,natual 是自然链)

- live:in-process FastAPI+白盒直置 bbs 可恢复态 + 用例编排 claim/attach/result 复刻 6 步 loop。
- natual:**真实后端 ``POST /api/task/execute`` 走框架 planner/dispatch 自然升 BBS**;接力 loop 由风清扬
  自身跑(``bbs-relay-pickup`` 用 ``exec``+HTTP 直调真实后端 ``/api/task/bbs/*``),用例只做
  provisioning + 提交 + 轮询 + 一次唤醒 + 断言。

# 设计约束

- owner 用独立 bot 名 ``task-owner-arch-bot`` + 通用 ``planning-arch`` skill,避免与
  ``test_task_integration_e2e.py`` 的 ``task-owner-bot``(装存储案例的 ``task-planning``)在同一
  singlebox 上共用 bot 造成 planning skill 串扰。
- 风清扬的 ``arch-analysis`` 是真实 LLM 推理,经 ``SingleboxEngineAdapter`` live 调用(唤醒 + 接力中段
  执行都是 live);风清扬本体由 ``SingleboxBotProvisioner`` 真实建 bot + 装 skill,幂等。
- ``SUB_DOMAINS`` 只取一个方向(基础架构):自然升 BBS + 风清扬自判 ``full`` 一段收口,演练"自然链接力"。
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
    SingleboxEngineAdapter,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "146836")
_TIMEOUT = float(os.environ.get("SINGLEBOX_TASK_E2E_TIMEOUT", "1500"))
# 独立 owner 名:避免与 test_task_integration_e2e.py 的 task-owner-bot(装存储案例 planning)共用 bot
_OWNER_BOT_NAME = "task-owner-arch-bot"
_FQY_BOT_NAME = "风清扬"

SKILLS_DIR = Path(__file__).parent / "skills"
_ARCH_PLANNING_SKILL = str(SKILLS_DIR / "planning-arch")
_SEARCH_SKILL = str(SKILLS_DIR / "search")
_ARCH_SKILL = str(SKILLS_DIR / "arch-analysis")
# bbs-relay-pickup skill 落在 spec 目录下(非 src/backend/skills);
# test 文件在 <repo>/src/backend/tests/community/core/task/singlebox_e2e/ ,parents[5] = <repo>/src/backend
_BBS_SKILL = str(
    Path(__file__).resolve().parents[5]
    / "specs" / "2026-08-09-task-goal-driven-task-runner-bbs" / "bbs-relay-pickup"
)

# 任务目标:整理支付宝公司内部「基础架构」方向技术架构师;只取一个方向,自然升 BBS + 风清扬一段收口。
TASK_ID = f"t_arch_{uuid.uuid4().hex[:6]}"
SUB_DOMAINS = ["基础架构"]
_BBS_MAX_DEPTH = 3  # 单方向一段收口;风清扬自判 full 一次唤醒即可

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}


def _execute_body(owner_id: str) -> dict:
    """``POST /api/task/execute`` 请求体(TaskInfoDTO):整理「基础架构」方向架构师。"""
    sub = SUB_DOMAINS[0]
    return {
        "task_spec": {
            "metadata": {
                "task_id": TASK_ID,
                "title": f"整理支付宝公司内部「{sub}」技术架构师",
                "instruction": (
                    f"整理支付宝(蚂蚁集团)内部「{sub}」方向的技术架构师/负责人清单,"
                    f"给出该方向架构师姓名/角色 + 职责。"
                ),
            },
            "context": {"background": "支付宝内部技术架构师梳理", "extend_props": {}},
            "goal": {
                "objective": f"整理支付宝公司内部「{sub}」方向技术架构师(姓名/角色/职责清单)",
                "acceptances": [
                    {"id": "ac1", "description": f"给出「{sub}」方向架构师清单(姓名或角色 + 职责)"},
                ],
            },
        },
        "source_channel_type": "bot",
        "source_channel_id": owner_id,
        "execution_config": {"MAX_DEPTH": 2, "BBS_MAX_DEPTH": _BBS_MAX_DEPTH},
    }


def _wake_prompt() -> str:
    """唤醒风清扬自驱 bbs-relay-pickup(用例只唤醒不代调 bbs/*)。"""
    return (
        "请执行 bbs-relay-pickup skill 完成 BBS 自主接力。\n"
        f"任务 task_id={TASK_ID} 已自然升 BBS(bbs_mode=true、根 PLANNING、图空闲),等待 bot 自主接力。\n"
        f"task API backend base url: {_BACKEND}(用 exec+curl 直调,例:"
        f"curl { _BACKEND }/api/task/list --json ...)。\n"
        "按 bbs-relay-pickup SKILL.md 6 步自驱:\n"
        "  步① GET /api/task/list 当客户端筛 bbs_mode==true,GET /api/task/dashboard 取整图;\n"
        "  步② POST /api/task/bbs/claim 占根;\n"
        "  步③ 读根 goal + 已 DONE 叶子 + 前序 scoped 节点 checkpoint 自判 full/partial/skip;\n"
        "  步④ POST /api/task/bbs/attach 挂一个 run_mode=bbs 节点 + 用你的 arch-analysis 能力执行该节点指令;\n"
        "  步⑤ POST /api/task/bbs/result 写回(完满则 root_verified=true 收口全图)→ claim 自动释放。\n"
        "本次目标单一方向,自判 full:一次唤醒做满剩余 → root_verified=true 收口全图 DONE。"
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
        #    风清扬 装 arch-analysis + bbs-relay-pickup(BBS 接力执行者)。
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        owner_id = await prov.create_bot(bot_name=_OWNER_BOT_NAME)
        await prov.install_skills(owner_id, [_ARCH_PLANNING_SKILL, _SEARCH_SKILL])
        fqy_id = await prov.create_bot(bot_name=_FQY_BOT_NAME)
        await prov.install_skills(fqy_id, [_ARCH_SKILL, _BBS_SKILL])
        await prov._aclose()
        print(f"[provision] owner={owner_id} ← planning-arch+search ; "
              f"风清扬={fqy_id} ← arch-analysis+bbs-relay-pickup")

        # 2) live adapter:用于唤醒风清扬自驱 bbs-relay-pickup(真实 LLM 推理)
        adapter = SingleboxEngineAdapter(backend_base_url=_BACKEND, user_id=_USER_ID)

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 3) POST /api/task/execute → backend 进程内真实 engine 推进:owner 规划 → search 派发 →
            #    候选不匹配 → on_miss@MAX_DEPTH 自然升 BBS(bbs_mode=True / 根 PLANNING / 图空闲)。
            r = await cli.post(f"{_BACKEND}/api/task/execute", json=_execute_body(owner_id))
            r.raise_for_status()
            print(f"[execute] {r.json().get('message')} data={r.json().get('data')}")

            # 等自然升 BBS:Poll 直到 bbs_mode 置 true(或全图 DONE / 超时)
            g: dict = {}
            deadline = time.monotonic() + _TIMEOUT
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
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} "
                      f"bbs_mode={(g.get('extend_props') or {}).get('bbs_mode')} nodes={snap}")
                if (g.get("extend_props") or {}).get("bbs_mode"):
                    break  # 已自然升 BBS
                if g.get("status") == "DONE":
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
            self.assertEqual(
                root.get("status"), "PLANNING",
                f"升 BBS 后根非可恢复态 PLANNING(可能落到图级 HUNG 硬终态,BBS 接不上);"
                f"根 status={root.get('status')} §10.5 seam 需关注 planning/MAX_DEPTH",
            )

            # 5) 唤醒风清扬自驱 bbs-relay-pickup:用例只唤醒,不代调 bbs/* 路由。
            #    一次唤醒 = 一段接力;未收口且图空闲则再唤醒,上限 BBS_MAX_DEPTH 次。
            wake_prompt = _wake_prompt()
            wakes = 0
            while g.get("status") not in ("DONE", "HUNG") and wakes < _BBS_MAX_DEPTH:
                wakes += 1
                print(f"[wake#{wakes}] 唤醒风清扬自驱 bbs-relay-pickup ...")
                try:
                    run = await adapter.send_and_wait_async(
                        bot_id=fqy_id, message=wake_prompt, timeout=600.0
                    )
                    status = run.get("status")
                    content = (run.get("result") or {}).get("content") or ""
                    print(f"[wake#{wakes}] status={status} content[:300]={content[:300]!r}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[wake#{wakes}] adapter 异常:{exc!r}")
                # 唤醒后轮询,等接力写回落地 / 图收口 / 图空闲可再唤醒
                sub_deadline = time.monotonic() + 300.0
                while time.monotonic() < sub_deadline:
                    r = await cli.get(f"{_BACKEND}/api/task/dashboard", params={"task_id": TASK_ID})
                    r.raise_for_status()
                    g = r.json().get("data") or {}
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

        # 6) 断言:自然升 BBS + 风清扬自主接力收口图 DONE
        try:
            await adapter._aclose()
        except Exception:
            pass

        self.assertEqual(g.get("status"), "DONE", f"全图未闭环 DONE:status={g.get('status')}")
        nodes = {t["node_id"]: t for t in g.get("tasks") or []}
        self.assertEqual(nodes[TASK_ID]["status"], "DONE", "根未 DONE")
        self.assertTrue((g.get("extend_props") or {}).get("bbs_mode"), "图未置 bbs_mode")

        bbs_nodes = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "bbs" and t["node_id"] != TASK_ID
        ]
        self.assertGreaterEqual(
            len(bbs_nodes), 1,
            f"无风清扬自驱的 bbs scoped 节点(检查 bbs-relay-pickup 是否被唤醒执行);"
            f"nodes={[t.get('node_id') for t in g.get('tasks') or []]}",
        )
        for n in bbs_nodes:
            ri = n.get("run_info") or {}
            self.assertEqual(n.get("status"), "DONE", f"scoped 未 DONE:{n.get('node_id')}")
            self.assertEqual(
                ri.get("assignee"), fqy_id,
                f"scoped 非 风清扬 接力:{n.get('node_id')} assignee={ri.get('assignee')}",
            )
            self.assertTrue(
                (ri.get("output") or {}).get("architects"),
                f"scoped 缺架构师 checkpoint:{n.get('node_id')}",
            )
        print(f"[final] graph={g.get('status')} 风清扬接力段={len(bbs_nodes)} 唤醒={wakes} 根=DONE")


def nodes_first_ext(g: dict, key: str) -> str:
    """取图 extend_props 上的 key(如 bbs_owner),缩略打印用。"""
    v = (g.get("extend_props") or {}).get(key)
    return str(v)[:24] if v else "-"


if __name__ == "__main__":
    unittest.main()