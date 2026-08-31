"""预发环境 e2e — 写作质检协同(state_machine),直连预发后端。

参考 ``singlebox_e2e/yaml/test_writing_qc_state_machine_e2e.py`` 的回路
(execute → 轮询 dashboard → DONE),但打**预发**:backend URL / 鉴权 / 预置 bot id 全走
环境变量,空缺即 skip(CI 安全)。

gated by ``AVERNET_PRE_TASK_E2E=1``;无需起 singlebox,直接打预发:

  AVERNET_PRE_TASK_E2E=1 \\
  AVERNET_PRE_BACKEND_URL=https://<预发host> \\
  AVERNET_E2E_USER_ID=<uid> \\
  AVERNET_E2E_WRITER_BOT_ID=<预置 writer bot id> \\
  AVERNET_E2E_EDITOR_BOT_ID=<预置 editor bot id> \\
  [AVERNET_E2E_COOKIE=<cookie文件路径 或 整段Cookie原文>] \\
  [AVERNET_PRE_TASK_E2E_TIMEOUT=2000] \\
  src/backend/.venv/bin/python -m pytest \\
    src/backend/tests/community/core/task/e2e/test_writing_qc_state_machine_pre_e2e_yaml.py -s

# 场景

复刻参考测的 yaml 协同模板(state_machine):draft → polish → finalize 线性。
经 ``POST /api/v1/collaboration/tasks/execute`` 提交 yaml 协同模板 + ``participant_bindings``
→ 预发 BCS 收群自动跑状态机 → 终态经回调 POST 回预发本后端
``/api/v1/collaboration/tasks/callback/report``(预发 ``api_base_url`` = corp overlay
``economy_governance.iframe_callback_url_pre`` 的 origin)→ ``TaskLoopCallback.report_result``
→ ``on_report`` → 图收敛。用例只做:提交 + 轮询 dashboard 观察 + 校验(与 singlebox 参考测同口径)。

# 预发接入默认(已确认)

- ``owner_bot_id`` / ``participant_bindings`` 用**纯 bot_id**(不带 singlebox 的 ``:user_id``
  透传 —— 那是 singlebox adapter 专属;预发真 BCS 走纯 id,后端解析身份)。
- bot 由预发预置,测试不自建(预发能否 ``create_bot`` 未知),id 走环境变量。
- 鉴权:``x-user_id`` 必填(且作 execute 体里 ``owner_user_id``);预发走 **cookie** 鉴权而非 bearer。
  ``AVERNET_E2E_COOKIE`` 值若指向一个**已存在文件**,从该文件读 cookie 内容(单行,去末尾换行);
  否则把值当 cookie 原文(``name1=v1; name2=v2``)。非空则加 ``Cookie`` 头。
- yaml 协同模板从 singlebox 参考测 import(单一信源,免漂移/免转抄)。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest

import httpx

# 复刻同一份 yaml 协同模板(单一信源):与 singlebox 参考测同源,免转抄/免漂移。
from tests.community.core.task.singlebox_e2e.yaml.test_writing_qc_state_machine_e2e import (
    WRITING_QC_YAML,
)

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
    """``POST /api/v1/collaboration/tasks/execute`` 请求体(预发版:纯 bot_id,无 :user_id 透传)。

    与 singlebox 参考测同构,但 ``owner_bot_id`` / ``participant_bindings`` 用纯 bot_id
    (预发真 BCS 由后端解析身份,不像 singlebox adapter 走 ``bot_id:user_id`` 透传)。
    """
    return {
        "task_spec": {
            "metadata": {
                "title": "写作质检协同",
                "instruction": "请按写作质检协同流程产出最终回复。",
            },
            "context": {
                "background": "写作质检协同预发 e2e:writer 产出初稿→editor 润色→editor 生成最终回复(线性)。",
                "extend_props": {},
            },
            "goal": {
                "objective": "就「远程办公协作工具的发展趋势」写一篇短文,经写作质检协同后产出最终回复。",
                "acceptances": [
                    {"id": "ac1", "acceptance": "最终回复包含结论、依据、风险和下一步建议"},
                    {"id": "ac2", "acceptance": "最终回复经质检通过且已润色"},
                ],
            },
        },
        "source_type": "bot",
        "owner_user_id": _USER_ID,
        # 纯 bot_id(预发真 BCS 解析身份,无 singlebox :user_id 透传)。
        "owner_bot_id": writer_id,
        "execution_config": {
            "task_type": "STATIC-GROUP-WORKFLOW",
            "yaml": WRITING_QC_YAML,
            "participant_bot_ids": [editor_id],
            "participant_bindings": {
                "writer": [writer_id],
                "editor": [editor_id],
            },
            "group_name": "写作质检协同群(预发e2e)",
        },
    }


@unittest.skipUnless(
    _READY,
    "设置 AVERNET_PRE_TASK_E2E=1 + AVERNET_PRE_BACKEND_URL + AVERNET_E2E_USER_ID + "
    "AVERNET_E2E_WRITER_BOT_ID + AVERNET_E2E_EDITOR_BOT_ID 启用预发 e2e",
)
class TestWritingQcStateMachinePreE2E(unittest.TestCase):
    def test_execute_yaml_state_machine_runs_to_done(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run())
        finally:
            loop.close()

    async def _run(self) -> None:
        print(
            f"[pre-e2e] backend={_BACKEND} user={_USER_ID} "
            f"writer={_WRITER_ID} editor={_EDITOR_ID} cookie={_COOKIE_SRC}"
        )
        g: dict = {}
        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 1) POST /execute → TaskService.execute → _run_yaml成立 BCS 协作群(预发自动跑状态机)
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

            # 2) 轮询 dashboard(回调服务收到的执行进度落图)直到任务结束 / 超时
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

        # 3) 输出 + 校验执行结果(与 singlebox 参考测同口径)
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

        self.assertEqual(
            g.get("status"), "DONE",
            f"全图未闭环 DONE:status={g.get('status')} (BCS 未自动运行状态机/未回投?)",
        )
        self.assertIsNotNone(root, "根节点(task_id)未出现")
        self.assertEqual(root.get("status"), "DONE", "根未 DONE")
        ri = root.get("run_info") or {}
        self.assertEqual(ri.get("run_mode"), "coop_group", "根非 coop_group")
        # 群 id 前缀容忍:真 BCS 产 bcs_grp_<uuid>;本地 stub/double 产 grp_<8hex>。
        self.assertTrue(
            str(ri.get("assignee") or "").startswith(("grp_", "bcs_grp_")),
            f"根 assignee 非群 id:{ri.get('assignee')!r}",
        )
        acceptance = ri.get("acceptance_result") or {}
        self.assertEqual(
            acceptance.get("verdict"), "DONE",
            f"根验收未 PASS:{acceptance} (BCS 回投 success=false?)",
        )
        output = ri.get("output")
        self.assertTrue(output, "根无最终输出(output 为空)")
        print(f"[result] output={str(output)!r}")


if __name__ == "__main__":
    unittest.main()
