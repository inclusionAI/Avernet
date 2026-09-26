"""预发环境 e2e — BBS 接力(task_type=bbs):主动 bid→select→claim→dispatch + 胜者回投收敛,直连预发后端。

回路:``POST /api/v1/collaboration/tasks/execute`` 提交 ``task_type=bbs`` 任务
→ ``TaskService._run_bbs``:根直接置 HUNG("创建BBS接力任务") + fire-and-forget 派发
  ``bbs_runner.notify(bid→select→claim_bbs_owner→把任务发给胜出 dream bot)``;
→ 胜者回投 ``POST /api/v1/collaboration/tasks/bbs/attach``(在根下挂 bbs-<8hex> scoped 子节点)
  + ``/bbs/result``(验收回投)→ ``on_bbs_report`` 收口(PASS→scoped SUCCESS→_on_pass_collect→plan(root)
  →has_gap=False→_maybe_finish_graph→图 SUCCESS)。
→ 用例只做:提交 + 轮询 dashboard 观察 + 校验 BBS 接力产物(bbs- 子节点 / run_mode==bbs / 接力收敛)。

与 yaml 协同(state_machine)是两条完全不同的链路:本测**不**校验 ``coop_group``/``grp_`` assignee
(那是 yaml 路径),改校验 BBS:出现 ``bbs-`` 子节点、该子节点 ``run_mode=="bbs"``、图收敛 ``SUCCESS`` 且
该 bbs 子节点 ``SUCCESS/PASS``。

gated by ``AVERNET_PRE_TASK_E2E=1``;无需起 singlebox,直接打预发:

  AVERNET_PRE_TASK_E2E=1 \\
  AVERNET_PRE_BACKEND_URL=https://<预发host> \\
  AVERNET_E2E_USER_ID=<uid> \\
  AVERNET_E2E_WRITER_BOT_ID=<任务 owner bot id> \\
  AVERNET_E2E_EDITOR_BOT_ID=<预置在预发的 dream bot id(需 BCS task_claim_mode + task_dream_mode 均开)> \\
  [AVERNET_E2E_COOKIE=<cookie文件路径 或 整段Cookie原文>] \\
  [AVERNET_PRE_TASK_E2E_TIMEOUT=3600] \\
  python3 -m pytest \\
    src/backend/tests/community/core/task/e2e/test_task_pre_e2e_bbs.py -s

# 预发接入默认(已确认)

- ``owner_bot_id`` 用**纯 bot_id**(不带 singlebox 的 ``:user_id`` 透传;预发真 BCS 走纯 id,后端解析身份)。
- ``AVERNET_E2E_EDITOR_BOT_ID`` 在此链路是 BBS 接力 dream bot 的**预置标识**(env 留痕 / skip 门控用),
  **不随请求体下发**——BBS 胜者由后端经 ``BcnService.list_bots_by_task_modes(claim=True, dream=True,
  match="all")`` 取 roster 决定;故该 dream bot 需在预发 BCS 上**同时开启** task_claim_mode=true 且
  task_dream_mode=true。两者缺一 → roster 命中 0 → bbs_runner 日志 ``无 dream bot 命中,留可恢复态``,
  接力不执行、图不收敛;port 未注入(bcn/bot None)→ 日志 ``skip: bcn/bot 缺失``。
- bot 由预发预置,测试不自建(预发能否 create_bot/patch task_mode 未知),id 走环境变量。
- 鉴权:``x-user_id`` 必填(且作 execute 体里 ``owner_user_id``);预发走 **cookie** 鉴权而非 bearer。
  ``AVERNET_E2E_COOKIE`` 值若指向一个**已存在文件**,从该文件读 cookie 内容(单行,去末尾换行);
  否则把值当 cookie 原文(name1=v1; name2=v2)。非空则加 ``Cookie`` 头。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest

import httpx

_LIVE = os.environ.get("AVERNET_PRE_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("AVERNET_PRE_BACKEND_URL", "").strip().rstrip("/")
_USER_ID = os.environ.get("AVERNET_E2E_USER_ID", "").strip()
_COOKIE_IN = os.environ.get("AVERNET_E2E_COOKIE", "").strip()
# 支持两种:AVERNET_E2E_COOKIE 指向已存在文件 → 从该文件读 cookie(单行,去末尾换行);
# 否则把值当 cookie 原文(name1=v1; name2=v2)。读失败兜底为空,不让 cookie 文件问题破坏测试收集。
if _COOKIE_IN and os.path.isfile(_COOKIE_IN):
    try:
        with open(_COOKIE_IN, "r", encoding="utf-8") as _f:
            _COOKIE = _f.read().strip()
        _COOKIE_SRC = "file"
    except OSError:
        _COOKIE = ""
        _COOKIE_SRC = "file-read-error"
elif _COOKIE_IN:
    _COOKIE = _COOKIE_IN
    _COOKIE_SRC = "inline"
else:
    _COOKIE = ""
    _COOKIE_SRC = "none"
_WRITER_ID = os.environ.get("AVERNET_E2E_WRITER_BOT_ID", "").strip()
_EDITOR_ID = os.environ.get("AVERNET_E2E_EDITOR_BOT_ID", "").strip()
_TIMEOUT = float(os.environ.get("AVERNET_PRE_TASK_E2E_TIMEOUT", "2000"))

_READY = bool(_LIVE and _BACKEND and _USER_ID and _WRITER_ID and _EDITOR_ID)

_HDRS: dict[str, str] = {"x-user-id": _USER_ID, "accept": "application/json"}
if _COOKIE:
    _HDRS["Cookie"] = _COOKIE


def _execute_body(writer_id: str, editor_id: str) -> dict:
    """``POST /api/v1/collaboration/tasks/execute`` 请求体(BBS 接力版:``task_type=bbs``)。

    ``task_type=bbs`` → ``TaskService._run_bbs``:根直接 HUNG("创建BBS接力任务")→ 派发 bbs_runner
    主动 bid→select→claim→把任务发给 dream 胜者。**不建 BCS 协作群、不挂 participant_bindings**
    (writer 是 owner;dream 胜者经 roster 决定,非固定 binding)。
    ``editor_id`` 仅作 dream roster 的预置标识(env / skip 门控用),不随请求体下发(``_ = editor_id`` 显式留痕)。
    """
    _ = editor_id  # dream 胜者由后端 roster 决定,不在请求体里绑定;保留入参向后兼容/留痕。
    return {
        "task_spec": {
            "metadata": {
                "title": "诗歌创作",
                "instruction": "创作一首赞美成都的诗歌",
            },
            "context": {
                "background": "诗歌大赛",
                "extend_props": {},
            },
            "goal": {
                "objective": "创作一首赞美成都的诗歌",
                "acceptances": [
                    {"id": "ac1", "acceptance": "朗朗上口、打动人心"}
                ],
            },
        },
        "source_type": "bot",
        "owner_user_id": _USER_ID,
        # 纯 bot_id(预发真 BCS 解析身份)。
        "owner_bot_id": writer_id,
        "execution_config": {
            "task_type": "bbs"
        },
    }


@unittest.skipUnless(
    _READY,
    "设置 AVERNET_PRE_TASK_E2E=1 + AVERNET_PRE_BACKEND_URL + AVERNET_E2E_USER_ID + "
    "AVERNET_E2E_WRITER_BOT_ID + AVERNET_E2E_EDITOR_BOT_ID 启用预发 e2e",
)
class TestWritingQcStateMachinePreE2E(unittest.TestCase):
    def test_execute_bbs_relay_runs_to_done(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    async def _run(self) -> None:
        print(
            f"[pre-e2e][bbs] backend={_BACKEND} user={_USER_ID} "
            f"writer(owner)={_WRITER_ID} editor(dream)={_EDITOR_ID} cookie={_COOKIE_SRC}"
        )
        g: dict = {}
        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 1) POST /execute → TaskService._run_bbs(task_type=bbs):根直接 HUNG("创建BBS接力任务")→
            #    后台 bbs_runner.notify 主动 bid→select→claim_bbs_owner→把任务发给胜出 dream bot;
            #    dream 胜者回投 /bbs/attach(挂 bbs-<8hex> scoped 子节点)+ /bbs/result(验收回投)
            #    → on_bbs_report 收口(PASS→scoped SUCCESS→_on_pass_collect→plan(root)→图 SUCCESS)。
            r = await cli.post(
                f"{_BACKEND}/api/v1/collaboration/tasks/execute",
                json=_execute_body(_WRITER_ID, _EDITOR_ID),
            )
            r.raise_for_status()
            body = r.json()
            data = body.get("data") or {}
            print(f"[execute] message={body.get('message')} data={data}")
            self.assertTrue(data.get("success"), f"execute 未成功:{data}")
            task_id = data.get("task_id")
            self.assertTrue(task_id, f"execute 响应缺 task_id:{data}")

            # 2) 轮询 dashboard(接力异步回调落图)直到图终态 / 超时
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                pr = await cli.get(
                    f"{_BACKEND}/api/v1/collaboration/tasks/dashboard",
                    params={"task_id": task_id},
                )
                pr.raise_for_status()
                g = pr.json().get("data") or {}
                tasks = g.get("tasks") or []
                snap = []
                for t in tasks:
                    ri = t.get("run_info") or {}
                    snap.append({
                        "node_id": t.get("node_id"),
                        "status": t.get("status"),
                        "run_mode": ri.get("run_mode") or "",
                        "assignee": str(ri.get("assignee") or "")[:32],
                        "extend_props": ri.get("extend_props") or {},
                        "exec_error": ri.get("exec_error"),
                        "verdict": (ri.get("acceptance_result") or {}).get("verdict"),
                    })
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} nodes={snap}")
                if g.get("status") in ("DONE", "HUNG"):
                    break
                await asyncio.sleep(6.0)

        # 3) 输出 + 校验 BBS 接力产物与接力收敛(对齐 on_bbs_report 收口径)
        print(f"[final] graph={g.get('status')} tasks={len(g.get('tasks') or [])}")
        nodes = {t.get("node_id"): t for t in g.get("tasks") or []}
        root = nodes.get(task_id)
        for nid, nd in nodes.items():
            ri = nd.get("run_info") or {}
            print(
                f"  - {str(nid):28} {str(nd.get('status')):8} "
                f"mode={ri.get('run_mode') or '-':11} "
                f"assignee={str(ri.get('assignee') or '-')[:24]} "
                f"verdict={(ri.get('acceptance_result') or {}).get('verdict')}"
            )

        # 接力收敛:图应在超时内达到终态(DONE=接力成功 / HUNG=接力失败/超限终态)。
        # 非终态(留 RUNNING)→ 异步接力链路没跑通(查后端 bbs_runner.notify 日志:
        # skip: bcn/bot 缺失 / 无 dream bot 命中 / 无有效 bid / bid 超时)。
        self.assertIn(
            g.get("status"), ("DONE", "HUNG"),
            f"接力未收敛(图非终态): graph={g.get('status')} loop={g.get('loop_round')} "
            f"(BBS 异步接力未触发/未回投? 见 bbs_runner.notify 日志)",
        )
        self.assertIsNotNone(root, "根节点(task_id)未出现")

        # ① 出现 BBS 接力子节点(bbs-<8hex>:attach_bbs_node 创建,run_mode=bbs)。
        bbs_nodes = [nd for nid, nd in nodes.items() if str(nid).startswith("bbs-")]
        self.assertTrue(
            bbs_nodes,
            f"未出现 bbs- 接力子节点: graph={g.get('status')} nodes={list(nodes)}",
        )
        bbs = bbs_nodes[0]
        bbs_ri = bbs.get("run_info") or {}

        # ② 接力子节点 run_mode == "bbs"。
        self.assertEqual(
            bbs_ri.get("run_mode"), "bbs",
            f"接力子节点 run_mode 非 bbs: {bbs_ri} node={bbs.get('node_id')}",
        )

        # ③ 接力收敛(成功):图 SUCCESS + bbs- 子节点 SUCCESS/PASS + 有最终输出。
        # (on_bbs_report PASS→scoped SUCCESS→_on_pass_collect→plan(root)→has_gap=False→图 SUCCESS)
        # graph=HUNG / bbs- 非 SUCCESS / 无 PASS:接力未成功(无有效胜者 / 验收 FAIL→on_bbs_report 删 scoped 节点)。
        self.assertEqual(g.get("status"), "SUCCESS", f"接力未成功收敛 DONE: graph={g.get('status')}")
        self.assertEqual(bbs.get("status"), "SUCCESS", f"接力子节点未 SUCCESS: status={bbs.get('status')}")
        bbs_acceptance = bbs_ri.get("acceptance_result") or {}
        self.assertEqual(
            bbs_acceptance.get("verdict"), "PASS",
            f"接力子节点验收未 PASS:{bbs_acceptance}",
        )
        bbs_output = bbs_ri.get("output")
        self.assertTrue(bbs_output, "接力子节点无最终输出(output 为空)")
        print(
            f"[result] bbs_node={bbs.get('node_id')} verdict={bbs_acceptance.get('verdict')} "
            f"output={str(bbs_output)!r}"
        )


if __name__ == "__main__":
    unittest.main()
