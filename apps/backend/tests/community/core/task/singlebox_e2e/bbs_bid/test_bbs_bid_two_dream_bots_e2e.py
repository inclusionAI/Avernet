"""BBS 主动触发(bid→select→claim→dispatch)两 dream-bot live singlebox e2e。

gated by ``SINGLEBOX_TASK_E2E=1``。本地 ``./scripts/singlebox.sh start all`` 起好 singlebox 后跑
(launcher 已设 ``AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE``,见 scripts/modules/bcs.sh / backend.sh;
改了 task_service/executor/bcs 适配/provisioner 后**务必重启后端**):

  SINGLEBOX_TASK_E2E=1 SINGLEBOX_USER_ID=35983 SINGLEBOX_BOT_ID=1 \
    src/backend/.venv/bin/python -m pytest \
      src/backend/tests/community/core/task/singlebox_e2e/bbs_bid/test_bbs_bid_two_dream_bots_e2e.py -s

# 场景(动 LLM 自评 bid + 真执行;参考 test_writing_qc_state_machine_e2e.py 的 live 手法)

任务 MISS→HUNG→升 BBS 后,引擎 ``_schedule_bbs_notify`` fire-and-forget ``start_run`` → ``bbs_runner.notify``:

1. **bid**:`list_bots_by_task_modes(dream=True)` 拉 dream-mode roster(本用例建**两个** dream bot 并开
   ``task_dream_mode``)→ 并发 ``send_and_wait_async`` 让每个 bot 自评 ``completion_rate``(真 LLM 出 JSON)。
2. **select + claim + dispatch**:选 completion_rate 最高者 → 引擎服务端 ``claim_bbs_owner(winner)``
   (recover 清 HUNG 死分支)→ 给 winner 派发 ``bbs-relay-single-task`` 任务消息。
3. **接力执行**:winner 读 dashboard 剩余事项 → ``bbs/attach`` 挂 ``run_mode=bbs`` scoped 节点 →
   用自身能力(arch-analysis)执行 → ``bbs/result`` 回投 PASS + architects → ``on_bbs_report`` 收口 → 根 SUCCESS → 图 SUCCESS。

# 前置:怎么让两个 dream bot 进 roster

BCS ``task_dream_mode`` 唯一 setter 是 principal-gated 的 openapi PATCH。本用例经
``SingleboxBotProvisioner.set_bbs_task_dream_mode``(新加)自铸 gateway principal token
(HS256/iss=gateway/aud=bcs/kid=bare,principals=[user(subject.id=user_id)]——user_id 即 bot 的 owner
staff_no,经 BCS ``authorize_bot_management`` 的 owner 匹配放行)PATCH
``/openapi/v1/collaboration/bots/{bot_uuid}``。signing key 取 env ``AVERNET_SECRET_PRINCIPAL_SIGNING_KEY_VALUE``
(singlebox launcher 默认 ``avernet-dev-signing-key-NOT-FOR-PROD``)。PATCH 非 2xx 抛错带 status/body。

# 确定性 vs LLM

- MISS→升 BBS 确定可复现:owner 装 ``planning-arch``(单一交付物「架构师名册」→ ``[N_architects]``)
  + ``search``(``N_architects``→MISS),``MAX_DEPTH=1`` → ``miss_depth_exhausted``。(同 bbs/ 目录 natual e2e 手法。)
- bid 自评 + 接力执行依赖真 LLM(winner 不固定,亦可能两 bot 自评相近);本用例**断言可观结果**:图经
  ``run_mode=bbs`` scoped 节点(其 assignee ∈ 两个 dream bot 之一)收口到 SUCCESS,即证明 bid→select→claim→
  dispatch→执行 链路打通。不校验具体 winner / completion_rate 数值。

# 已修的接缝

``bbs_runner.notify`` 经 BBS executor 取 ``execution_graph.task_id``,而 ``TaskExecutionGraph`` 原无该字段
→ live 触发即 ``AttributeError``(单测用 MagicMock.mask)。本仓已给 ``TaskExecutionGraph`` 加 ``task_id``
(initialize / query_task_dashboard 子树投影透传),使真触发链可用。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest
from pathlib import Path

import httpx

from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    SingleboxBotProvisioner,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "35983")
_TIMEOUT = float(os.environ.get("SINGLEBOX_TASK_E2E_TIMEOUT", "2000"))

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

# skill 目录:本文件在 <repo>/src/backend/tests/community/core/task/singlebox_e2e/bbs_bid/
SKILLS_DIR = Path(__file__).resolve().parent / "../skills"
_PLANNING_SKILL = str(SKILLS_DIR / "planning-arch")   # 确定式规划(单一交付物→[N_architects])
_SEARCH_SKILL = str(SKILLS_DIR / "search")            # 派发决策表(N_architects→MISS)
_ARCH_SKILL = str(SKILLS_DIR / "arch-analysis")       # dream bot 中继执行侧(产架构师名册)
# bbs-relay-single-task 在 spec 目录:parents[6]=<repo>/src/backend
_BBS_SINGLE_TASK_SKILL = str(
    Path(__file__).resolve().parents[6]
    / "specs" / "2026-08-09-task-goal-driven-bbs-active-relay" / "bbs-relay-single-task"
)

_OWNER_BOT_NAME = "e2e-bbs-bid-owner"
_DREAM_A_BOT_NAME = "e2e-bbs-bid-dream-a"
_DREAM_B_BOT_NAME = "e2e-bbs-bid-dream-b"

# 独立 owner 名避免与其它 e2e 的 task-owner-* 共用 bot 造成 skill 串扰


# 单次 dashboard 读超时:engine 在 threading.RLock 内跨长 LLM 调用,query_task_dashboard 同锁,
# 写路径持锁跑规划/派发/bid 时读会排队。短超时 + 外层重试,熬过一次性排队(同 bbs/ 目录 natual e2e)。
_DASH_TIMEOUT = 60.0


async def _get_dashboard(cli: httpx.AsyncClient, task_id: str) -> dict | None:
    """读 dashboard;一次性排队/断网返 None 供外层重试(不直接 fail 用例)。"""
    try:
        r = await cli.get(f"{_BACKEND}/api/v1/collaboration/tasks/dashboard",
                          params={"task_id": task_id}, timeout=_DASH_TIMEOUT)
    except (httpx.TimeoutException, httpx.RequestError) as exc:
        print(f"[dashboard] 读超时/网络异常,稍后重试:{exc!r}")
        return None
    if r.status_code != 200:
        print(f"[dashboard] 非 200(status={r.status_code}),稍后重试:{r.text[:120]!r}")
        return None
    return r.json().get("data") or {}


def _execute_body(owner_id: str) -> dict:
    """``POST /api/v1/collaboration/tasks/execute``(内部副本,免 gateway spanner)请求体:
    单一交付物「架构师名册」+ MAX_DEPTH=1 → planning-arch 产 [N_architects] → search MISS → 升 BBS → bid。
    """
    return {
        "task_spec": {
            "metadata": {
                "task_id": "bbs_bid_two_dream",
                "title": "整理基础架构方向架构师名册",
                "instruction": (
                    "整理基础架构方向的 3 位核心技术架构师,给出每位架构师的姓名/角色 + 主要职责。"
                    "这是一个**单一交付的人才名册**,不要按子方向再拆分。基于自身知识即可,不联网。"
                ),
            },
            "context": {"background": "基础架构方向架构师梳理", "extend_props": {}},
            "goal": {
                "objective": "整理基础架构方向 3 位核心架构师(姓名/角色 + 职责)",
                "acceptances": [
                    {"id": "ac_arch", "description": "给出基础架构方向 3 位架构师的姓名/角色 + 职责"},
                ],
            },
        },
        "source_type": "bot",
        "owner_user_id": _USER_ID,
        "owner_bot_id": owner_id,
        # task_type=dynamic(LLM 自规划);MAX_DEPTH=1:depth-1 miss 直走 miss_depth_exhausted 升 BBS(不 re-plan 嵌套)
        "execution_config": {"task_type": "dynamic", "MAX_DEPTH": 1, "BBS_MAX_DEPTH": 3},
    }


@unittest.skip(
    "singlebox roster deferred: list_bots_by_task_modes moved to BcnService "
    "(unified BcnConfig provider identity, exercised in pre/prod); singlebox BBS bid e2e deferred"
)
class TestBbsBidTwoDreamBotsE2E(unittest.TestCase):
    def test_miss_to_bbs_two_dream_bots_bid_and_execute_to_done(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) provisioning:
        #    owner ← planning-arch + search(确定式规划/派发:N_architects MISS→升 BBS);
        #    两个 dream bot ← arch-analysis + bbs-relay-single-task(BBS 主动 bid 候选 + 中继执行),
        #    各 onboard_to_bcn + set_bcs_visibility(public) + set_bbs_task_dream_mode(True)(进 dream roster)。
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        try:
            owner_id = await prov.create_bot(bot_name=_OWNER_BOT_NAME)
            await prov.install_skills(owner_id, [_PLANNING_SKILL, _SEARCH_SKILL])
            dream_a_id = await prov.create_bot(bot_name=_DREAM_A_BOT_NAME)
            dream_b_id = await prov.create_bot(bot_name=_DREAM_B_BOT_NAME)
            for bid, skills in (
                (dream_a_id, [_ARCH_SKILL, _BBS_SINGLE_TASK_SKILL]),
                (dream_b_id, [_ARCH_SKILL, _BBS_SINGLE_TASK_SKILL]),
            ):
                await prov.install_skills(bid, skills)
                # onboard / visibility 幂等可重入(已就绪/竟态 → 跳过不阻断);dream-mode 开关失败必须显形
                # (401/403 时 set_bbs_task_dream_mode 抛 RuntimeError 带 status/body,不被此处吞成"跳过")
                for meth in ("onboard_to_bcn", "set_bcs_visibility"):
                    fn = getattr(prov, meth, None)
                    if fn is None:
                        continue
                    try:
                        await fn(bid)
                    except Exception as exc:  # noqa: BLE001 已就绪/竟态 → 跳过
                        print(f"[provision] {meth}({bid}) 跳过:{exc!r}")
                #await prov.set_bbs_task_dream_mode(bid)  # 进 dream roster;失败即抛(带诊断)
        finally:
            try:
                await prov._aclose()
            except Exception:  # noqa: BLE001
                pass
        print(f"[provision] owner={owner_id} ← planning-arch+search ; "
              f"dream_a={dream_a_id} dream_b={dream_b_id} ← arch-analysis+bbs-relay-single-task(+dream_mode)")
        dream_ids = {dream_a_id, dream_b_id}

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 2) POST /api/v1/collaboration/tasks/execute → 真实 engine 推进:
            #    planning-arch → [N_architects] → search MISS → @MAX_DEPTH=1 miss_depth_exhausted → 升 BBS
            #    → _schedule_bbs_notify → bbs_runner.notify → 两 dream bot bid → 选 winner → claim → dispatch
            #    → winner(bbs-relay-single-task) attach→execute→result → 收口 SUCCESS。
            r = await cli.post(f"{_BACKEND}/api/v1/collaboration/tasks/execute",
                               json=_execute_body(owner_id))
            r.raise_for_status()
            body = r.json()
            data = body.get("data") or {}
            print(f"[execute] message={body.get('message')} data={data}")
            self.assertTrue(data.get("success"), f"execute 未成功:{data}")
            task_id = data.get("task_id")
            self.assertTrue(task_id, f"execute 响应缺 task_id:{data}")

            # 3) 轮询 dashboard 直到任务结束 / 超时
            g: dict = {}
            escalated_seen = False
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                g = await _get_dashboard(cli, task_id)
                if g is None:
                    await asyncio.sleep(5.0)
                    continue
                ep = g.get("extend_props") or {}
                snap = [
                    (t.get("node_id"), t.get("status"),
                     (t.get("run_info") or {}).get("run_mode") or "",
                     str((t.get("run_info") or {}).get("assignee") or "")[:24])
                    for t in g.get("tasks") or []
                ]
                if ep.get("bbs_mode") and not escalated_seen:
                    escalated_seen = True
                    print(f"[escalated] ⭐ MISS→升 BBS! bbs_mode=True loop={g.get('loop_round')} "
                          f"bbs_owner={ep.get('bbs_owner')} nodes={snap} → 引擎将 bid 两 dream bot")
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} "
                      f"bbs_mode={ep.get('bbs_mode')} nodes={snap}")
                if g.get("status") in ("DONE", "HUNG"):
                    break
                await asyncio.sleep(6.0)

        # 4) 校验执行结果
        print(f"[final] graph={g.get('status')} tasks={len(g.get('tasks') or [])}")
        for t in g.get("tasks") or []:
            ri = t.get("run_info") or {}
            print(f"  - {str(t.get('node_id')):32} {str(t.get('status')):8} "
                  f"mode={ri.get('run_mode') or '-':11} "
                  f"assignee={str(ri.get('assignee') or '-')[:24]} "
                  f"verdict={(ri.get('acceptance_result') or {}).get('verdict')}")

        self.assertEqual(g.get("status"), "SUCCESS",
                         f"全图未闭环 DONE:status={g.get('status')}"
                         f"(未升 BBS / dream roster 空 / bid 全失败 / winner 未执行? 看 [snapshot] 定位)")
        self.assertTrue((g.get("extend_props") or {}).get("bbs_mode"), "未升 BBS(bbs_mode 未置 true)")

        nodes = {t.get("node_id"): t for t in g.get("tasks") or []}
        root = nodes.get(task_id)
        self.assertIsNotNone(root, "根节点(task_id)未出现")
        self.assertEqual(root.get("status"), "SUCCESS", f"根未 SUCCESS:{root.get('status')}")

        # bbs scoped 中继节点:winner 经 bbs-relay-single-task 挂的 run_mode=bbs 节点,assignee ∈ 两 dream bot
        scoped = [n for n in g.get("tasks") or []
                  if n.get("node_id") != task_id and (n.get("run_info") or {}).get("run_mode") == "bbs"]
        self.assertTrue(scoped, "无 run_mode=bbs scoped 中继节点(winner 未 attach/执行? bid 可能未 dispatch)")
        self.assertEqual(len(scoped), 1, f"应恰 1 个 bbs scoped 中继节点(1 个 winner 执行段):{[(n.get('node_id')) for n in scoped]}")
        sc = scoped[0]
        ri = sc.get("run_info") or {}
        self.assertEqual(sc.get("status"), "SUCCESS", f"scoped 中继节点未 SUCCESS:{sc.get('status')}")
        self.assertIn(ri.get("assignee"), dream_ids,
                      f"scoped assignee 非两 dream bot 之一(bid 未选到 dream bot?):{ri.get('assignee')}")
        ar = ri.get("acceptance_result") or {}
        self.assertEqual(ar.get("verdict"), "DONE", f"scoped 中继段验收未 PASS:{ar}")
        self.assertTrue(ri.get("output"), "scoped 中继段无最终输出(architects 产出为空)")
        # claim 已释放(收口后根 bbs_owner 清空)
        self.assertIsNone((root.get("run_info") or {}).get("extend_props", {}).get("bbs_owner"),
                           "收口后根 bbs_owner 未释放")
        # recover 清掉了原 MISS 的 HUNG 死分支 N_architects(bbs 接力视为推倒重做)
        self.assertNotIn("N_architects", nodes, "claim recover 未清 HUNG 死分支 N_architects")
        print(f"[result] winner={ri.get('assignee')} scoped=DONE/PASS 图=DONE")


if __name__ == "__main__":
    unittest.main()
