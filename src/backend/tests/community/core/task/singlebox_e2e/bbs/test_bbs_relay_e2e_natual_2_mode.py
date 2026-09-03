"""BBS 2-mode live e2e(natural):LLM 自规划,一子任务匹配到现成 bot、一子任务 MISS→HUNG→BBS 中继。

gated by ``SINGLEBOX_TASK_E2E=1``。本地 ``./scripts/singlebox.sh start all`` 起好 singlebox 后:

  SINGLEBOX_TASK_E2E=1 \
    src/backend/.venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/bbs/test_bbs_relay_e2e_natual_2_mode.py -s

# 剧本(natural 2-mode:依赖 LLM 自规划 + 真实匹配)

- 主任务含**两份交付物**:① 基础架构方向技术栈概览;② 基础架构方向 3 位架构师名册。
- **owner bot 装 ``planning-arch`` + ``search``**(同 integration e2e 的 storage search):
  - 规划走 ``planning-arch``(确定式按 ``t_2mode_`` 前缀查表)→ 拆出固定 2 子:`N_tech_stack`/`N_architects`;
  - 派发走 ``search`` skill(**按 `demand.node_id` 查表**,同 storage 方式):``N_tech_stack``→HIT_SINGLE
    `技术栈概览Bot`、``N_architects``→MISS。HIT/MISS 由表定(不靠 catalog 判),`bot_id` 在 catalog 按 `bot_name` 解析。
- 预期正好:**技术栈概览**子任务 ``N_tech_stack`` 查表 → HIT_SINGLE ``技术栈概览Bot`` → single_bot 正常执行;
  **架构师名册**子任务匹配不到任何 bot → MISS;``MAX_DEPTH=1`` → ``miss_depth_exhausted`` → 节点 HUNG →
  自然升 BBS(``bbs_mode=True``、根 ``PLANNING``、图空闲,spec §10.5 可恢复态)。
- 升 BBS 后唤醒一次金庸(``bbs-relay-pickup``):claim(recover 清掉 HUNG 死分支)→ 自判"架构师名册"段
  full → 挂 ``run_mode="bbs"`` scoped 节点 → ``arch-analysis`` mock 执行 → ``bbs/result`` 写回
  ``output_patch.architects`` → scoped SUCCESS。owner 复核根 gap 两份交付物齐 → 根 DONE → 图 SUCCESS。

# 为什么用 storage `search`(同 integration e2e)

派发(``SearchBasedDispatchStrategy``)把 ``[task-search]`` prompt 投给 owner bot,由 owner 上的 ``search`` skill 决出执行者。
**不装 search → owner 无 skill 应答 ``[task-search]`` → 全 MISS → 子任务全 HUNG**。本用例与 integration e2e 一样装
``skills/search``,该 skill 的确定式表除 storage 行外已**追加 arch 场景行**(``N_tech_stack``/``N_architects``)——
HIT/MISS 按 ``demand.node_id`` 查表(catalog 仅按 `bot_name` 解析 `bot_id`,需 jieba 分词命中,后端 venv 已装 jieba)。
storage 行不动 → integration e2e / natual 不受影响。

# 与 ``test_bbs_relay_e2e_natual.py`` 的区别

- natual:单一交付物,owner 装 planning-arch+search → ``N_architects`` 查表 MISS → 全 BBS,金庸一段收口。
- **2-mode natural**:两份交付物,owner 装 planning-arch+search(表里追加 arch 行)→ ``N_tech_stack`` 查表 HIT
  (single_bot)、``N_architects`` 查表 MISS→HUNG→BBS 中继(bbs);断言图里**同时**存在 single_bot 的 HIT 节点与 bbs scoped 中继节点。

# skill 说明

全程用已有 skill(``planning-arch`` / ``search`` / ``acceptance`` / ``arch-analysis`` / ``bbs-relay-pickup``),
不新增;``search`` 表里追加 arch 场景 node_id 行,storage 行不动。
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
# 独立 owner 名:避免与其它 e2e 的 task-owner-* 共用 bot 造成 skill 串扰
_OWNER_BOT_NAME = "task-owner-2mode-bot"
# N_hit 的现成执行者:bot_name「技术栈概览Bot」,与 search 表 N_tech_stack→技术栈概览Bot 对名(catalog 按名解析 bot_id)
_HIT_BOT_NAME = "技术栈概览Bot"
_JY_BOT_NAME = "金庸"

SKILLS_DIR = Path(__file__).parent / "../skills"
_PLANNING_SKILL = str(SKILLS_DIR / "planning-arch")  # 通用 LLM 规划(非 case 剧本)
_SEARCH_SKILL = str(SKILLS_DIR / "search")           # 派发决策 storage search(同 integration e2e;表里已加 arch 场景 node_id 行)
_ACCEPTANCE_SKILL = str(SKILLS_DIR / "acceptance")   # N_hit worker 自验收
_ARCH_SKILL = str(SKILLS_DIR / "arch-analysis")      # N_miss 升 BBS 后金庸中继执行侧 mock
# bbs-relay-pickup skill 落在 spec 目录下(非 src/backend/skills);
# test 文件在 <repo>/src/backend/tests/community/core/task/singlebox_e2e/bbs/ ,parents[6] = <repo>/src/backend
_BBS_SKILL = str(
    Path(__file__).resolve().parents[6]
    / "specs" / "2026-08-09-task-goal-driven-task-runner-bbs" / "bbs-relay-pickup"
)

# 主任务:两份交付物(技术栈概览 + 架构师名册);planning-arch LLM 自拆 ~2 子。
TASK_ID = f"t_2mode_{uuid.uuid4().hex[:6]}"
_BBS_MAX_DEPTH = 3  # 架构师名册一段中继收口;金庸自判 full 一次唤醒即可

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

# 单次 dashboard 读超时:engine 在 threading.RLock 内跨长 LLM 调用,query_task_dashboard 同锁,
# 写路径持锁跑规划/派发时读会排队(可远超常规读时延)。短超时 + 外层轮询重试,熬过一次性排队。
_DASH_TIMEOUT = 60.0


async def _get_dashboard(cli: httpx.AsyncClient, task_id: str) -> dict | None:
    """读 ``/openapi/v1/collaboration/tasks/dashboard``;一次性排队/断网时返 ``None`` 供外层轮询重试(不直接 fail 用例)。"""
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
    """``POST /openapi/v1/collaboration/tasks/execute`` 请求体(TaskInfoDTO):基础架构方向「技术栈概览 + 架构师名册」两份交付物。

    planning-arch 按 ``t_2mode_`` 前缀查表拆固定 2 子:N_tech_stack(查表 HIT ``技术栈概览Bot``)、
    架构师名册(无匹配 bot → MISS)。``MAX_DEPTH=1`` → 架构师名册 MISS@depth-1 直走 miss_depth_exhausted 升 BBS。
    """
    return {
        "task_spec": {
            "metadata": {
                "task_id": TASK_ID,
                "title": "整理某某某公司基础架构方向:技术栈概览 + 架构师名册",
                "instruction": (
                    "本任务有**两份交付物**,请拆成两个子任务分别完成:"
                    "1) 给出基础架构方向的技术栈概览(计算/存储/网络等分层与核心组件);"
                    "2) 整理某某某公司基础架构方向的 3 位核心技术架构师(姓名/角色 + 职责)。"
                    "基于自身知识即可,不联网。"
                ),
            },
            "context": {"background": "某某某公司基础架构方向梳理", "extend_props": {}},
            "goal": {
                "objective": (
                    "产出某某某公司基础架构方向:技术栈概览(分层+核心组件) + 3 位核心架构师名册(姓名/角色+职责)"
                ),
                "acceptances": [
                    {"id": "ac1", "description": "给出基础架构方向技术栈概览(计算/存储/网络等层与核心组件)"},
                    {"id": "ac2", "description": "给出基础架构方向 3 位架构师的姓名/角色 + 职责"},
                ],
            },
        },
        "source_type": "bot",
        "owner_bot_id": owner_id,
        # MAX_DEPTH=1:架构师名册子任务在 depth-1 MISS 直走 miss_depth_exhausted 升 BBS(不 re-plan 嵌套);
        # 技术栈概览子任务同为 depth-1,命中 bot 正常执行不受影响。
        "execution_config": {"MAX_DEPTH": 1, "BBS_MAX_DEPTH": _BBS_MAX_DEPTH},
    }


def _wake_prompt(jy_bot_id: str) -> str:
    """唤醒金庸自驱 bbs-relay-pickup 收口"架构师名册"侧(MISS→HUNG→升 BBS 的那段)。

    只交代用哪个 skill 接单 + 必要定位信息(task_id / backend url / 自身 bot_id),
    不复述 skill 内部的 6 步流程。金庸读 dashboard 自判:**技术栈概览已由现成 bot 做完、剩余"架构师名册"
    这段它 full 能做** → 挂 bbs scoped 节点 → arch-analysis 执行 → bbs/result 写回。

    必须传入金庸自身 bot_id(否则误填引擎身份);另钉**交付物编码契约**:step⑤ ``output_patch`` 必含
    ``architects`` 键(架构师名册数组),本用例据此断言 scoped 节点 ``run_info.output.architects``。
    """
    return (
        "请用 bbs-relay-pickup skill 接力执行已自然升 BBS 的单子。\n"
        f"task_id={TASK_ID};task API backend base url={_BACKEND};"
        f"你(金庸)自身 bot_id={jy_bot_id}(claim/attach/result 的 bot_id 字段填它)。\n"
        "交付物编码:本次中继段做的是「架构师名册」,step⑤ bbs/result 的 output_patch 必须含 architects 键"
        "(数组,装整理出的架构师名册,每项至少含姓名/角色/职责);这是本段交付物的固定写入口。"
    )


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox live e2e")
class TestBbsRelayE2ENatual2Mode(unittest.TestCase):
    def test_one_hit_one_miss_to_bbs(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) provisioning(幂等建 bot + 装 skill):
        #    owner 装 planning-arch(arch 确定式规划)+ search(同 integration e2e,表里追加 arch 行);
        #    技术栈 bot(现成执行者)装 acceptance(N_hit worker 自验收,用自身 LLM 知识产技术栈概览);
        #    金庸(中继)装 arch-analysis(N_miss 执行侧 mock)+ bbs-relay-pickup。
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        owner_id = await prov.create_bot(bot_name=_OWNER_BOT_NAME)
        await prov.install_skills(owner_id, [_PLANNING_SKILL, _SEARCH_SKILL])  # planning-arch 规划 + search(表驱动派发)
        hit_id = await prov.create_bot(bot_name=_HIT_BOT_NAME)
        await prov.install_skills(hit_id, [_ACCEPTANCE_SKILL])
        jy_id = await prov.create_bot(bot_name=_JY_BOT_NAME)
        await prov.install_skills(jy_id, [_ARCH_SKILL, _BBS_SKILL])
        await prov._aclose()
        print(f"[provision] owner={owner_id} ← planning-arch+search ; "
              f"技术栈bot={hit_id} ← acceptance ; "
              f"金庸={jy_id} ← arch-analysis+bbs-relay-pickup")

        # 2) live adapter:用于唤醒金庸自驱 bbs-relay-pickup(真实 LLM 推理)
        adapter = SingleboxEngineAdapter(backend_base_url=_BACKEND, user_id=_USER_ID)

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 3) POST /openapi/v1/collaboration/tasks/execute → backend 进程内真实 engine 推进:
            #    planning-arch LLM 自拆 ~2 子 → owner 通用 LLM 派发判:技术栈概览命中技术栈bot(HIT single_bot)、
            #    架构师名册无匹配 bot(MISS)→ 后者 @MAX_DEPTH=1 升 BBS(bbs_mode=True / 根 PLANNING / 图空闲;
            #    技术栈概览在跑保根可恢复)。
            r = await cli.post(f"{_BACKEND}/openapi/v1/collaboration/tasks/execute", json=_execute_body(owner_id))
            r.raise_for_status()
            print(f"[execute] {r.json().get('message')} data={r.json().get('data')}")

            # 等自然升 BBS:Poll 直到 bbs_mode 置 true(架构师名册 MISS→HUNG→升 BBS)或全图 SUCCESS / 超时
            g: dict = {}
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                g = await _get_dashboard(cli, TASK_ID)
                if g is None:
                    await asyncio.sleep(5.0)
                    continue
                snap = [
                    (t.get("node_id"), t.get("status"),
                     (t.get("run_info") or {}).get("run_mode") or "",
                     str((t.get("run_info") or {}).get("assignee") or "")[:24])
                    for t in g.get("tasks") or []
                ]
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} "
                      f"bbs_mode={(g.get('extend_props') or {}).get('bbs_mode')} nodes={snap}")
                if (g.get("extend_props") or {}).get("bbs_mode"):
                    _ep = g.get("extend_props") or {}
                    _nodes = {t["node_id"]: t for t in g.get("tasks") or []}
                    _root = _nodes.get(TASK_ID)
                    print(
                        f"[escalated] ⭐ 架构师名册 MISS→已自然升 BBS! task={TASK_ID} "
                        f"graph={g.get('status')} loop_round={g.get('loop_round')} "
                        f"bbs_relay_count={_ep.get('bbs_relay_count')} "
                        f"root.status={(_root or {}).get('status')} "
                        f"node_count={len(g.get('tasks') or [])}"
                    )
                    for _t in g.get("tasks") or []:
                        _ri = _t.get("run_info") or {}
                        print(f" 已自然升BBS  - {_t.get('node_id'):28} {_t.get('status'):9} "
                              f"mode={_ri.get('run_mode') or '-':5} "
                              f"assignee={str(_ri.get('assignee') or '')[:24]}")
                    break
                if g.get("status") == "SUCCESS":
                    break  # 未升 BBS 已闭环(异常路径,留待断言揭出)
                await asyncio.sleep(5.0)

            # 4) 断言架构师名册 MISS→自然升 BBS 落到可恢复态(spec §10.5):bbs_mode=True
            self.assertTrue(
                (g.get("extend_props") or {}).get("bbs_mode"),
                f"架构师名册未自然升 BBS(bbs_mode 未置 true);看快照定位 planning-arch 分解 / owner 派发判。"
                f"graph={g.get('status')}",
            )

            # 5) 唤醒金庸自驱 bbs-relay-pickup 收口"架构师名册"侧:
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
                        continue
                    snap = [
                        (t.get("node_id"), t.get("status"),
                         (t.get("run_info") or {}).get("run_mode") or "",
                         str((t.get("run_info") or {}).get("assignee") or "")[:24])
                        for t in g.get("tasks") or []
                    ]
                    print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} "
                          f"bbs_owner={nodes_first_ext(g,'bbs_owner')} nodes={snap}")
                    if g.get("status") in ("DONE", "HUNG"):
                        break
                    busy = any((t.get("status") == "RUNNING") for t in g.get("tasks") or [])
                    held = (g.get("extend_props") or {}).get("bbs_owner")
                    if not busy and not held:
                        break
                    await asyncio.sleep(5.0)

        # 6) 断言:2-mode natural 混合结局——一子任务真匹配 bot(single_bot HIT)+ 一子任务 MISS→BBS 中继(bbs)
        #    + 图收口 SUCCESS。断言取宽容(≥1):LLM 自分解/自判非确定,只要 HIT 与中继共存即达到 2-mode 意图。
        try:
            await adapter._aclose()
        except Exception:
            pass

        self.assertEqual(g.get("status"), "SUCCESS", f"全图未闭环 DONE:status={g.get('status')}")
        nodes = {t["node_id"]: t for t in g.get("tasks") or []}
        self.assertEqual(nodes[TASK_ID]["status"], "SUCCESS", "根未 SUCCESS")
        self.assertTrue((g.get("extend_props") or {}).get("bbs_mode"), "图未置 bbs_mode(架构师名册未升 BBS)")

        # 6a) HIT 侧:一子任务真匹配到现成 bot(single_bot,DONE,assignee=技术栈bot)
        hit_nodes = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "single_bot" and t["node_id"] != TASK_ID
        ]
        self.assertGreaterEqual(
            len(hit_nodes), 1,
            f"无 single_bot 派发节点(技术栈概览子任务未真匹配到技术栈bot;看 owner 派发判 / 候选预查 token)。"
            f"nodes={[t.get('node_id') for t in g.get('tasks') or []]}",
        )
        for n in hit_nodes:
            ri = n.get("run_info") or {}
            self.assertEqual(n.get("status"), "SUCCESS", f"HIT 子任务未 SUCCESS:{n.get('node_id')}")
            self.assertEqual(
                ri.get("assignee"), hit_id,
                f"HIT 子任务非技术栈bot 执行:{n.get('node_id')} assignee={ri.get('assignee')}",
            )

        # 6b) 中继侧:架构师名册 MISS→BBS,金庸自驱 bbs scoped 节点(DONE,assignee=金庸,output.architects)
        bbs_nodes = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "bbs" and t["node_id"] != TASK_ID
        ]
        self.assertGreaterEqual(
            len(bbs_nodes), 1,
            f"无金庸自驱的 bbs scoped 节点(架构师名册未升 BBS/未中继);"
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
        print(f"[final] graph={g.get('status')} HIT(single_bot)={len(hit_nodes)} "
              f"中继(bbs)={len(bbs_nodes)} 唤醒={wakes} 根=SUCCESS")


def nodes_first_ext(g: dict, key: str) -> str:
    """取图 extend_props 上的 key(如 bbs_owner),缩略打印用。"""
    v = (g.get("extend_props") or {}).get(key)
    return str(v)[:24] if v else "-"


if __name__ == "__main__":
    unittest.main()