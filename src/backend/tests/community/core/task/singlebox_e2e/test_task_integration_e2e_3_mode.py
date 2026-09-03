"""3-mode live e2e(natural):LLM 自规划,3 子任务分别走 single_bot / coop_group / BBS 三种执行模态。

gated by ``SINGLEBOX_TASK_E2E=1``。**协作群需 BCS double**,本地起后端 singlebox 时务必设:

  SINGLEBOX_BCS_DOUBLE=1 SINGLEBOX_TASK_E2E=1 \
    src/backend/.venv/bin/python -m pytest \
      tests/community/core/task/singlebox_e2e/test_task_integration_e2e_3_mode.py -s

# 剧本(natural 3-mode:依赖 LLM 自规划 + 真实匹配,三种 run_mode 共存)

- 主任务含**三份交付物**:
  ① 基础架构方向技术栈概览 → 命中现成 bot 走 **single_bot**;
  ② 业务架构与数据架构双视角深度分析 → 命中协作群走 **coop_group**(2 bot);
  ③ 基础架构方向 3 位架构师名册 → 无现成 bot → MISS → HUNG → 升 **BBS** 中继。
- **owner bot 装 ``planning-arch`` + ``search``**(同 integration e2e 的 storage search):
  - 规划走 ``planning-arch``(确定式按根验收交付物集合查表)→ 拆出固定 3 子:`N_tech_stack`/`N_dual_view`/`N_architects`;
  - 派发走 ``search`` skill(**按 `demand.node_id` 查表**,同 storage 方式):表里 ``N_tech_stack``→HIT_SINGLE
    `技术栈概览Bot`、``N_dual_view``→HIT_MULTI_BOTS 两视角 bot、``N_architects``→MISS。HIT/MISS 由表定(不靠 catalog 判),
    `bot_id` 在 catalog 里按 `bot_name` 解析。
- 预期:① 命中 ``技术栈概览Bot`` → HIT_SINGLE single_bot;② 命中 ``业务架构视角Bot``+``数据架构视角Bot``
  → HIT_MULTI_BOTS coop_group(BCS double 拉群 → completed→PASS);③ 无匹配 → MISS;``MAX_DEPTH=1``
  → ``miss_depth_exhausted`` → 节点 HUNG → 自然升 BBS(``bbs_mode=True``、根 ``PLANNING``、图空闲)。
- 升 BBS 后唤醒一次金庸(``bbs-relay-pickup``):claim(recover 清 HUNG 死分支)→ 自判"架构师名册"段 full
  → 挂 ``run_mode="bbs"`` scoped 节点 → ``arch-analysis`` mock 执行 → ``bbs/result`` 写回
  ``output_patch.architects`` → scoped SUCCESS。owner 复核根 gap 三份交付物齐 → 根 DONE → 图 SUCCESS。

# 为什么用 storage `search`(同 integration e2e)

派发(``SearchBasedDispatchStrategy``)把 ``[task-search]`` prompt 投给 owner bot,由 owner 上的 ``search`` skill 决出执行者。
**不装 search → owner 无 skill 应答 ``[task-search]`` → 全 MISS → 子任务全 HUNG**(实测 3 子/4 子全 HUNG 即此)。
本用例与 integration e2e 一样装 ``skills/search``,只不过该 skill 的确定式表除 storage 行外,已**追加 arch 场景行**
(``N_tech_stack``/``N_dual_view``/``N_architects``,见 skill 末尾)——HIT/MISS 按 ``demand.node_id`` 查表,**不靠 catalog 判**
(catalog 仅用来按 `bot_name` 解析真实 `bot_id`,需 jieba 分词命中,后端 venv 已装 jieba)。storage 行不动 → integration e2e / natual 不受影响。

# 命名约束(让 BBS 那子任务必 MISS)

协作群两 bot 命名 ``业务架构视角Bot`` / ``数据架构视角Bot``(含「架构」**不含**「架构师」),技术栈 bot
``技术栈概览Bot``。BBS 那子任务是「架构师名册」,token「架构师」LIKE 匹配 bot_name —— 上述 bot 名里
**都没有「架构师」子串** → 该子任务候选 catalog 为空 → owner 判 MISS → 升 BBS。若给协作 bot 命名
含「架构师」(如 业务架构师Bot),则「架构师名册」会命中它们 → 不 MISS、不升 BBS,破坏 3-mode。

# 与 ``test_task_integration_e2e.py`` 的区别

- integration e2e:storage case-scripted skill(planning+search),~8 子任务覆盖三模态,确定式映射;
- **3-mode natural**:``planning-arch``(arch 确定式表)+ ``search``(同 integration e2e,表里追加 arch 行),固定 3 子
  (N_tech_stack/N_dual_view/N_architects)+ 按 node_id 查表派发,三模态共存。全程用已有 skill,不新增。
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
    SingleboxEngineAdapter,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "35983")
_TIMEOUT = float(os.environ.get("SINGLEBOX_TASK_E2E_TIMEOUT", "2000"))
# 独立 owner 名:避免与其它 e2e 的 task-owner-* 共用 bot 造成 skill 串扰
_OWNER_BOT_NAME = "task-owner-3mode-bot"
# ① single_bot 现成执行者:bot_name 含「技术栈概览」,供该子任务候选命中
_SINGLE_BOT_NAME = "技术栈概览Bot"
# ② coop_group 两成员:含「架构」**不含**「架构师」,使「架构师名册」子任务不命中它们 → 必 MISS→BBS
_COOP_BOT_A = "业务架构视角Bot"
_COOP_BOT_B = "数据架构视角Bot"
_JY_BOT_NAME = "金庸"

SKILLS_DIR = Path(__file__).parent / "skills"  # 本文件在 singlebox_e2e/ 下,skills 即同级 ./skills
_PLANNING_SKILL = str(SKILLS_DIR / "planning-arch")  # 通用 LLM 规划(非 case 剧本)
_SEARCH_SKILL = str(SKILLS_DIR / "search")           # 派发决策 storage search(同 integration e2e;表里已加 arch 场景 node_id 行)
_ACCEPTANCE_SKILL = str(SKILLS_DIR / "acceptance")   # worker / 群成员 自验收
_ARCH_SKILL = str(SKILLS_DIR / "arch-analysis")      # BBS 那段金庸中继执行侧 mock
# bbs-relay-pickup skill 落在 spec 目录下(非 src/backend/skills);
# 本文件在 <repo>/src/backend/tests/community/core/task/singlebox_e2e/ ,parents[5] = <repo>/src/backend
_BBS_SKILL = str(
    Path(__file__).resolve().parents[5]
    / "specs" / "2026-08-09-task-goal-driven-task-runner-bbs" / "bbs-relay-pickup"
)

# 主任务:三份交付物(技术栈概览 + 业务/数据双视角分析 + 架构师名册);planning-arch LLM 自拆 ~3 子。
_BBS_MAX_DEPTH = 3  # 架构师名册一段中继收口;金庸自判 full 一次唤醒即可

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}

# 单次 dashboard 读超时:engine 在 threading.RLock 内跨长 LLM 调用,query_task_dashboard 同锁,
# 写路径持锁跑规划/派发时读会排队(可远超常规读时延)。短超时 + 外层轮询重试,熬过一次性排队。
_DASH_TIMEOUT = 60.0


async def _get_dashboard(cli: httpx.AsyncClient, task_id: str) -> dict | None:
    """读 ``/api/v1/collaboration/tasks/dashboard``;一次性排队/断网时返 ``None`` 供外层轮询重试(不直接 fail 用例)。"""
    try:
        r = await cli.get(
            f"{_BACKEND}/api/v1/collaboration/tasks/dashboard",
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


def _print_task_details(g: dict | None, task_id: str) -> None:
    """经 dashboard 接口取到任务详情后,打印图状态 + 每节点完整 run_info(extend_props/output/verdict/exec_error)
    便于 e2e 排查三模态落地与收口情况。"""
    if not g:
        print(f"[task-details] task={task_id} (empty dashboard)")
        return
    ep = g.get("extend_props") or {}
    print(f"[task-details] task={task_id} graph.status={g.get('status')} "
          f"loop_round={g.get('loop_round')} bbs_mode={ep.get('bbs_mode')} "
          f"bbs_owner={str(ep.get('bbs_owner') or '-')[:24]} "
          f"bbs_relay_count={ep.get('bbs_relay_count')} node_count={len(g.get('tasks') or [])}")
    for t in g.get("tasks") or []:
        ri = t.get("run_info") or {}
        print(f"  - node={t.get('node_id')} status={t.get('status')} "
              f"run_mode={ri.get('run_mode') or '-'} assignee={str(ri.get('assignee') or '-')[:40]}")
        if ri.get("extend_props"):
            print(f"      extend_props={ri.get('extend_props')}")
        if ri.get("exec_error"):
            print(f"      exec_error={ri.get('exec_error')}")
        ar = ri.get("acceptance_result") or {}
        if ar:
            print(f"      acceptance={ar}")
        out = ri.get("output")
        if out:
            print(f"      output={str(out)[:300]!r}")


def _signature(g: dict | None) -> tuple:
    """图态签名(graph.status + bbs_mode/bbs_owner + 各节点 status/run_mode/assignee),用于检测轮询间状态变化,
    变化时触发 ``_print_task_details`` 打印(提升 task-details 打印频率,不只在最后打一次)。"""
    if not g:
        return ()
    ep = g.get("extend_props") or {}
    nodes = tuple(
        (t.get("node_id"), t.get("status"),
         (t.get("run_info") or {}).get("run_mode"),
         str((t.get("run_info") or {}).get("assignee") or "")[:24])
        for t in g.get("tasks") or []
    )
    return (g.get("status"), ep.get("bbs_mode"), str(ep.get("bbs_owner") or "")[:24], nodes)


def _execute_body(owner_id: str) -> dict:
    """``POST /api/v1/collaboration/tasks/execute`` 请求体(TaskInfoDTO):基础架构方向三份交付物。

    planning-arch(通用 LLM)读根 goal 自拆 ~3 子:技术栈概览(命中 技术栈概览Bot → single_bot HIT)、
    业务+数据双视角分析(命中 业务架构视角Bot+数据架构视角Bot → coop_group HIT_MULTI_BOTS)、
    架构师名册(无匹配 bot → MISS)。``MAX_DEPTH=1`` → 架构师名册 MISS@depth-1 直走 miss_depth_exhausted 升 BBS。
    """
    return {
        "task_spec": {
            "metadata": {
                "title": "整理某某某公司基础架构方向:技术栈概览 + 业务/数据双视角分析 + 架构师名册",
                "instruction": (
                    "本任务有**三份交付物**,请拆成三个子任务分别完成:"
                    "1) 给出基础架构方向的技术栈概览(计算/存储/网络等分层与核心组件);"
                    "2) 从**业务架构与数据架构双视角**深度分析该公司基础架构的现状与演进"
                    "(需业务架构、数据架构两个视角的专家协作完成);"
                    "3) 整理某某某公司基础架构方向的 3 位核心技术架构师(姓名/角色 + 职责)。"
                    "基于自身知识即可,不联网。"
                ),
            },
            "context": {"background": "某某某公司基础架构方向梳理", "extend_props": {}},
            "goal": {
                "objective": (
                    "产出某某某公司基础架构方向:技术栈概览 + 业务/数据双视角架构分析 + 3 位核心架构师名册"
                ),
                "acceptances": [
                    {"id": "ac1", "acceptance": "给出基础架构方向技术栈概览(计算/存储/网络等层与核心组件)"},
                    {"id": "ac2", "acceptance": "从业务架构与数据架构双视角深度分析基础架构现状与演进"},
                    {"id": "ac3", "acceptance": "给出基础架构方向 3 位架构师的姓名/角色 + 职责"},
                ],
            },
        },
        "source_type": "bot",
        "owner_user_id": _USER_ID,
        "owner_bot_id": owner_id,
        # MAX_DEPTH=1:架构师名册子任务在 depth-1 MISS 直走 miss_depth_exhausted 升 BBS(不 re-plan 嵌套);
        # single_bot / coop_group 子任务同为 depth-1,命中执行不受影响。BBS拉群需 SINGLEBOX_BCS_DOUBLE=1。
        "execution_config": {
            "task_type": "dynamic",
            "MAX_DEPTH": 1,
            "BBS_MAX_DEPTH": _BBS_MAX_DEPTH,
        },
    }


def _wake_prompt(task_id: str, jy_bot_id: str) -> str:
    """唤醒金庸自驱 bbs-relay-pickup 收口"架构师名册"侧(MISS→HUNG→升 BBS 的那段)。

    只交代用哪个 skill 接单 + 必要定位信息(task_id / backend url / 自身 bot_id),
    不复述 skill 内部 6 步流程。金庸读 dashboard 自判:**技术栈概览 + 双视角分析已由现成 bot/群做完、
    剩余"架构师名册"这段它 full 能做** → 挂 bbs scoped 节点 → arch-analysis 执行 → bbs/result 写回。

    必须传入金庸自身 bot_id(否则误填引擎身份);另钉**交付物编码契约**:step⑤ ``output_patch`` 必含
    ``architects`` 键(架构师名册数组),本用例据此断言 scoped 节点 ``run_info.output.architects``。
    """
    return (
        "请用 bbs-relay-pickup skill 接力执行已自然升 BBS 的单子。\n"
        f"task_id={task_id};task API backend base url={_BACKEND};"
        f"你(金庸)自身 bot_id={jy_bot_id}(claim/attach/result 的 bot_id 字段填它)。\n"
        "交付物编码:本次中继段做的是「架构师名册」,step⑤ bbs/result 的 output_patch 必须含 architects 键"
        "(数组,装整理出的架构师名册,每项至少含姓名/角色/职责);这是本段交付物的固定写入口。"
    )


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox live e2e")
class TestTaskIntegrationE2E3Mode(unittest.TestCase):
    def test_single_bot_coop_group_and_bbs(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) provisioning(幂等建 bot + 装 skill,全部用已有 skill、原样、不新增):
        #    owner 装 planning-arch(arch 确定式规划)+ search(同 integration e2e,表里追加 arch node_id 行);
        #    技术栈 bot(single_bot 执行者)装 acceptance;
        #    业务/数据双视角 bot(coop_group 两成员)装 acceptance;(命名含「架构」不含「架构师」,见文件头)
        #    金庸(BBS 中继)装 arch-analysis + bbs-relay-pickup。
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        owner_id = await prov.create_bot(bot_name=_OWNER_BOT_NAME)
        await prov.install_skills(owner_id, [_PLANNING_SKILL, _SEARCH_SKILL])  # planning-arch 规划 + search(表驱动派发,同 integration e2e)
        single_id = await prov.create_bot(bot_name=_SINGLE_BOT_NAME)
        await prov.install_skills(single_id, [_ACCEPTANCE_SKILL])
        coop_a_id = await prov.create_bot(bot_name=_COOP_BOT_A)
        await prov.install_skills(coop_a_id, [_ACCEPTANCE_SKILL])
        await prov.onboard_to_bcn(coop_a_id)  # 入网 BCN:coop_group 成员建群前必须,否则 form_coop_group 404 bot_not_found({bot_id}:{owner} 不在 BCN)
        await prov.set_bcs_visibility(coop_a_id)  # 设 BCS visibility=public(只此 bot):ensure_reachable 跳过好友校验,否则 protected 撞 403 not friends
        coop_b_id = await prov.create_bot(bot_name=_COOP_BOT_B)
        await prov.install_skills(coop_b_id, [_ACCEPTANCE_SKILL])
        await prov.onboard_to_bcn(coop_b_id)  # 同上(协作群两成员都要在 BCN)
        await prov.set_bcs_visibility(coop_b_id)  # 同上
        jy_id = await prov.create_bot(bot_name=_JY_BOT_NAME)
        await prov.install_skills(jy_id, [_ARCH_SKILL, _BBS_SKILL])
        await prov._aclose()
        print(f"[provision] owner={owner_id} ← planning-arch+search ; "
              f"技术栈bot={single_id} ← acceptance ; "
              f"业务视角bot={coop_a_id}+数据视角bot={coop_b_id} ← acceptance(协作群两成员) ; "
              f"金庸={jy_id} ← arch-analysis+bbs-relay-pickup")

        # 2) live adapter:用于唤醒金庸自驱 bbs-relay-pickup(真实 LLM 推理)
        adapter = SingleboxEngineAdapter(backend_base_url=_BACKEND, user_id=_USER_ID)

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 3) POST /api/v1/collaboration/tasks/execute → backend 进程内真实 engine 推进:
            #    planning-arch LLM 自拆 ~3 子 → owner 通用 LLM 派发判:
            #      技术栈概览 → HIT_SINGLE 技术栈概览Bot(single_bot);
            #      业务+数据双视角 → HIT_MULTI_BOTS [业务架构视角Bot,数据架构视角Bot](coop_group,BCS 拉群);
            #      架构师名册 → MISS → @MAX_DEPTH=1 升 BBS(bbs_mode=True / 根 PLANNING / 图空闲;前两子在跑保根可恢复)。
            r = await cli.post(f"{_BACKEND}/api/v1/collaboration/tasks/execute", json=_execute_body(owner_id))
            r.raise_for_status()
            execute_data = r.json().get("data") or {}
            task_id = execute_data["task_id"]
            print(f"[execute] {r.json().get('message')} data={execute_data}")

            # 等自然升 BBS:Poll 直到 bbs_mode 置 true(架构师名册 MISS→HUNG→升 BBS)或全图 SUCCESS / 超时
            g: dict = {}
            last_sig: tuple | None = None
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                g = await _get_dashboard(cli, task_id)
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
                if _signature(g) != last_sig:
                    last_sig = _signature(g)
                    _print_task_details(g, task_id)
                if (g.get("extend_props") or {}).get("bbs_mode"):
                    _ep = g.get("extend_props") or {}
                    _nodes = {t["node_id"]: t for t in g.get("tasks") or []}
                    _root = _nodes.get(task_id)
                    print(
                        f"[escalated] ⭐ 架构师名册 MISS→已自然升 BBS! task={task_id} "
                        f"graph={g.get('status')} loop_round={g.get('loop_round')} "
                        f"bbs_relay_count={_ep.get('bbs_relay_count')} "
                        f"root.status={(_root or {}).get('status')} "
                        f"node_count={len(g.get('tasks') or [])}"
                    )
                    for _t in g.get("tasks") or []:
                        _ri = _t.get("run_info") or {}
                        print(f" 已自然升BBS  - {_t.get('node_id'):28} {_t.get('status'):9} "
                              f"mode={_ri.get('run_mode') or '-':11} "
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
            #    BBS 一次唤醒即收口(自判"架构师名册"段 full → 挂 bbs scoped 节点 → 写回),不多次唤醒。
            wake_prompt = _wake_prompt(task_id, jy_id)
            wakes = 0
            while g.get("status") not in ("DONE", "HUNG") and wakes < 1:
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
                    g = await _get_dashboard(cli, task_id)
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
                    if _signature(g) != last_sig:
                        last_sig = _signature(g)
                        _print_task_details(g, task_id)
                    if g.get("status") in ("DONE", "HUNG"):
                        break
                    busy = any((t.get("status") == "RUNNING") for t in g.get("tasks") or [])
                    held = (g.get("extend_props") or {}).get("bbs_owner")
                    if not busy and not held:
                        break
                    await asyncio.sleep(5.0)

            # 任务详情:经 dashboard 接口取全量并打印(便于 e2e 排查三模态落地)
            detail_g = await _get_dashboard(cli, task_id)
            if detail_g is not None:
                g = detail_g
            _print_task_details(g, task_id)

        # 6) 断言:3-mode natural 三种执行模态共存 + 图收口 SUCCESS。
        #    断言取宽容(≥1 每模态):LLM 自分解/自判非确定,只要三模态共存即达到 3-mode 意图。
        try:
            await adapter._aclose()
        except Exception:
            pass

        self.assertEqual(g.get("status"), "SUCCESS", f"全图未闭环 DONE:status={g.get('status')}")
        nodes = {t["node_id"]: t for t in g.get("tasks") or []}
        self.assertEqual(nodes[task_id]["status"], "SUCCESS", "根未 SUCCESS")
        self.assertTrue((g.get("extend_props") or {}).get("bbs_mode"), "图未置 bbs_mode(架构师名册未升 BBS)")

        # 6a) single_bot:技术栈概览子任务真匹配到现成 bot(DONE,assignee=技术栈概览Bot)
        single_nodes = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "single_bot" and t["node_id"] != task_id
        ]
        self.assertGreaterEqual(
            len(single_nodes), 1,
            f"无 single_bot 派发节点(技术栈概览子任务未真匹配到技术栈概览Bot;看 owner 派发判 / 候选预查)。"
            f"nodes={[t.get('node_id') for t in g.get('tasks') or []]}",
        )
        for n in single_nodes:
            ri = n.get("run_info") or {}
            self.assertEqual(n.get("status"), "SUCCESS", f"single_bot 子任务未 SUCCESS:{n.get('node_id')}")
            self.assertEqual(
                ri.get("assignee"), single_id,
                f"single_bot 非技术栈概览Bot 执行:{n.get('node_id')} assignee={ri.get('assignee')}",
            )

        # 6b) coop_group:业务+数据双视角子任务命中协作群(DONE,assignee=grp_ 群 id)
        coop_nodes = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "coop_group" and t["node_id"] != task_id
        ]
        self.assertGreaterEqual(
            len(coop_nodes), 1,
            f"无 coop_group 派发节点(双视角子任务未拉协作群;看 owner 是否判 HIT_MULTI_BOTS / BCS double 是否开)。"
            f"nodes={[t.get('node_id') for t in g.get('tasks') or []]}",
        )
        for n in coop_nodes:
            self.assertEqual(n.get("status"), "SUCCESS", f"coop_group 子任务未 SUCCESS:{n.get('node_id')}")
            self.assertTrue(
                # 群 id 前缀容忍两种后端:本地 stub/double 产 ``grp_<8hex>``;真 BCS(:21000)产 ``bcs_grp_<uuid>``。
                str((n.get("run_info") or {}).get("assignee") or "").startswith(("grp_", "bcs_grp_")),
                f"coop_group assignee 非群 id:{n.get('node_id')} assignee={(n.get('run_info') or {}).get('assignee')}",
            )

        # 6c) bbs:架构师名册 MISS→BBS,金庸自驱 bbs scoped 节点(DONE,assignee=金庸,output.architects)
        bbs_nodes = [
            t for t in g.get("tasks") or []
            if (t.get("run_info") or {}).get("run_mode") == "bbs" and t["node_id"] != task_id
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
        print(f"[final] graph={g.get('status')} single_bot={len(single_nodes)} "
              f"coop_group={len(coop_nodes)} bbs={len(bbs_nodes)} 唤醒={wakes} 根=SUCCESS")


def nodes_first_ext(g: dict, key: str) -> str:
    """取图 extend_props 上的 key(如 bbs_owner),缩略打印用。"""
    v = (g.get("extend_props") or {}).get(key)
    return str(v)[:24] if v else "-"


if __name__ == "__main__":
    unittest.main()
