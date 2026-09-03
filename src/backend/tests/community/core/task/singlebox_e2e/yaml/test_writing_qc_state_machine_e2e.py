"""YAML 协同模板(state_machine)端到端 — 写作质检协同。

gated by ``SINGLEBOX_TASK_E2E=1``。本地起好 singlebox 后跑(改了 task_service/executor/bcs 适配后**务必重启后端**):

  ./scripts/singlebox.sh start all
  SINGLEBOX_TASK_E2E=1 DEPLOY_PROFILE=singlebox \
    src/backend/.venv/bin/python -m pytest \
      src/backend/tests/community/core/task/singlebox_e2e/yaml/test_writing_qc_state_machine_e2e.py -s

# 场景

经 ``TaskService.execute(task_type=YAML)`` 直接提交一份写作质检协同 yaml 协作模板:

- writer 产出初稿 → editor 润色 → editor 生成最终回复(finalize)。离线 singlebox(llm.type=none)
  无 LLM judge,故去掉自动质检/修订分支,改线性流程(bot 由 _run 运行时 create_bot 建出)。
- 创建 bcn 协作群时,逻辑角色→产品 bot 的绑定(``participant_bindings``)为**创建群接口的入参**
  (非 yaml 模板内字段),经 ``execution_config`` 透传:``_run_yaml`` 把它塞进
  ``GroupFormation.extend_props``,``TaskExecutor.form_coop_group`` 注入 BCS ``create_group``
  (``participant_bindings``)。群 master 复用底层 ``driver_bot``(bot_ids[0]=owner),不另设字段。
- singlebox 真实 BCS(:21000)收群后**自动运行状态机**(本后端不调 ``start_state_machine_run``),状态机
  终态经回调 POST 回 ``apiBaseUrl``(本后端 :8888 的 ``/api/v1/collaboration/tasks/callback/*``)
  → ``TaskLoopCallback.report_result`` → ``on_report`` → 图收敛。
- 用例只做:提交 + 轮询 dashboard(观察回调服务收到的执行进度)直到任务结束 + 输出并校验执行结果。

# 设计约束

- owner/driver = writer(bot_92c2f019);故 ``owner_bot_id`` 取 writer,``participant_bot_ids`` 只列 editor
  (群 master 即 driver_bot=writer,复用底层字段)。
- ``participant_bindings`` = ``{writer:[bot_92c2f019], editor:[bot_9c4ff73d]}``(列表短形式),
  ``TaskExecutor._state_machine_bindings`` 会归一为 ``{role:{source,bot_ids}}`` 并解析成 BCS UUID。
- API base url 解析自 ``SINGLEBOX_BACKEND_URL``(singlebox profile),即 BCS 回投目标 = 本后端。
"""
from __future__ import annotations

import asyncio
import os
import time
import unittest

import httpx

from agentclaw.community.core.task.task_runner.client.singlebox_engine_adapter import (
    SingleboxBotProvisioner,
)

_LIVE = os.environ.get("SINGLEBOX_TASK_E2E", "").strip() in {"1", "true"}
_BACKEND = os.environ.get("SINGLEBOX_BACKEND_URL", "http://localhost:8888")
_USER_ID = os.environ.get("SINGLEBOX_USER_ID", "35983")
_TIMEOUT = float(os.environ.get("SINGLEBOX_TASK_E2E_TIMEOUT", "2000"))

# 实际 bot 由 _run 运行时经 SingleboxBotProvisioner.create_bot(bot_name=...) 建出并取回 id
# (服务端生成 bot_id,无法指定固定 id;create_bot 按 bot_name 幂等复用),writer 兼 owner/driver/master,editor 兼润色/最终回复。

# 写作协同模板(仅描述协同策略;逻辑角色 writer/editor 的实际 bot 由创建群接口的
# participant_bindings 绑定,不在 yaml 内)。离线 singlebox(llm.type=none)无 LLM judge,
# 故去掉自动质检/修订分支,线性 draft→polish→finalize。
WRITING_QC_YAML = """\
# 写作质检协同模板。
# 这个 YAML 只描述协同策略，不存储具体 bot id。
# 创建群时，通过 CreateGroupRequest.participant_bindings 把下面的逻辑角色绑定到实际 bot。

# name 是这个协同模板面向产品展示的名称。
name: 写作质检协同

metadata:
  # description 用来说明模板目标，不参与运行时调度。
  description: 写作者先产出候选回答，编辑润色后由负责人生成最终回复（离线 singlebox 无 LLM judge，线性流程）。

# participants 声明逻辑角色槽位。节点里的 assignee.binding 必须引用这些 key。
# 实际 bot 由群运行时 binding 提供，BCS 会根据 driver_bot 自动推导 BCS participant role。
participants:
  # writer 负责生成初稿，并在质检未通过时修订。
  writer:
    display_name: 写作者
    description: 负责生成初稿，并在质量检查未通过时完成修订。
    required: true

  # editor 负责润色已通过的初稿，并生成最终回复。
  editor:
    display_name: 编辑
    description: 负责润色通过质检的初稿，并生成面向用户的最终回复。
    required: true

runtime:
  # 结构化协同模板固定使用 state_machine。
  kind: state_machine
  state_machine:
    defaults:
      # node_timeout_ms 是每个节点等待 bot 最终响应的默认时间，单位是毫秒。
      # 节点没有单独配置 node_timeout_ms 时，会继承这个值。
      node_timeout_ms: 120000
      # max_attempts 是节点失败后的默认尝试次数。
      # 节点没有单独配置 max_attempts 时，会继承这个值。
      max_attempts: 2

    # nodes 定义工作流图。每个 map key 都是稳定的节点 id。
    nodes:
      # 入口节点：生成候选回答。离线 singlebox(llm.type=none)无 LLM judge,
      # 故去掉自动质检/修订分支,改线性 draft→polish→finalize。
      draft:
        # kind 固定为 bot_task，表示 BCS 会向指定 bot 发送指令，并等待最终响应。
        kind: bot_task
        # display_name 是面向产品展示的节点名称，不是节点 id。
        display_name: 生成初稿
        # 初稿是最关键的节点，保留默认尝试次数，但缩短单次超时时间。
        node_timeout_ms: 90000
        max_attempts: 2
        assignee:
          # type 固定为 bot_binding，表示运行时通过 participants 解析实际 bot。
          type: bot_binding
          # binding 必须引用一个 participant 槽位 key。
          binding: writer
        # instruction 会发送给被分配的 bot，不能为空。
        instruction: |
          请根据用户输入生成一版候选方案。
          输出需要包含：结论、依据、风险和下一步建议。
        # transitions 定义当前节点完成后的流转目标。线性进入润色。
        transitions:
          complete:
            targets:
              - polish

      # 通过路径：对已通过质检的初稿做轻量润色。
      polish:
        kind: bot_task
        display_name: 润色初稿
        assignee:
          type: bot_binding
          binding: editor
        # 润色时应保持原结论不变，不引入新的大方向。
        instruction: |
          初稿已通过质检。请基于初稿内容做轻量润色。
          保持原结论，不要引入新的大方向。
        transitions:
          complete:
            # 通过和修订路径最终都汇聚到 finalize。
            targets:
              - finalize

      # 终点节点：基于实际运行过的路径生成最终回答。
      finalize:
        kind: bot_task
        display_name: 生成最终回复
        assignee:
          type: bot_binding
          binding: editor
        # 这个节点产出最终回答。
        instruction: |
          请汇总已完成路径的输出，生成最终答复。
        # final_output 表示这个节点的产物是本次协同运行的最终输出。
        final_output: true
"""

_HDRS = {"x-user-id": _USER_ID, "accept": "application/json"}


def _execute_body(writer_id: str, editor_id: str) -> dict:
    """``POST /api/v1/collaboration/tasks/execute`` 请求体(内部副本,免 gateway spanner)。

    execution_config 透传创建群入参:``participant_bindings`` / ``participant_bot_ids``
    (ExecutionConfigDTO extra=allow),``_run_yaml`` 把 participant_bindings 塞进 GroupFormation.extend_props。
    群 master 复用 driver_bot(=owner_bot_id),不另传。

    execute 端直接以 BCS ``bot_id:owner_id`` 透传身份(owner_id = provisioner 的 user_id = 本测试 ``_USER_ID``,
    与 ``singlebox_engine_adapter.set_bcs_visibility`` 的 ``f"{bot_id}:{user_id}"`` 同源),触发 ``resolve_many``
    对带 ``:`` 的透传、不再查 BotService。provision 端(``onboard_to_bcn`` / ``set_bcs_visibility``)仍用纯 bot_id
    (内部自行拼 ``:{user_id}``),故此处只改发给 execute 的入参,不动 provisioner 调用。
    """
    writer = f"{writer_id}:{_USER_ID}"
    editor = f"{editor_id}:{_USER_ID}"
    return {
        "task_spec": {
            "metadata": {
                "title": "写作质检协同",
                "instruction": "请按写作质检协同流程产出最终回复。",
            },
            "context": {
                "background": "写作质检协同 yaml e2e:writer 产出初稿→自动质检→通过则 editor 润色/"
                              "未通过则 writer 修订→editor 生成最终回复。",
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
        # owner/driver = writer;participant_bot_ids 只列 editor(writer 已在 owner 槽)。
        "owner_bot_id": writer,
        "execution_config": {
            "task_type": "STATIC-GROUP-WORKFLOW",
            "yaml": WRITING_QC_YAML,
            "participant_bot_ids": [editor],
            # 逻辑角色→产品 bot 绑定(创建群接口 participant_bindings 入参)。
            "participant_bindings": {
                "writer": [writer],
                "editor": [editor],
            },
            # 群 master 复用底层 driver_bot(=owner_bot_id=writer),不另设 master_bot。
            "group_name": "写作质检协同群",
        },
    }


@unittest.skipUnless(_LIVE, "设置 SINGLEBOX_TASK_E2E=1 启用真实 singlebox e2e")
class TestWritingQcStateMachineE2E(unittest.TestCase):
    def test_execute_yaml_state_machine_runs_to_done(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(self._run(loop))
        finally:
            loop.close()

    async def _run(self, loop: asyncio.AbstractEventLoop) -> None:
        # 1) 自洽建 writer/editor bot(create_bot 服务端生成 id,按 bot_name 幂等复用)→ 入网 BCN + 开 BCS 可见。
        #    不再依赖外部预置的固定 id bot;singlebox 清库后也能重建(与其它 singlebox e2e 同手法)。
        prov = SingleboxBotProvisioner(backend_base_url=_BACKEND, user_id=_USER_ID)
        try:
            writer_id = await prov.create_bot(bot_name="e2e-writing-qc-writer")
            editor_id = await prov.create_bot(bot_name="e2e-writing-qc-editor")
            for bid in (writer_id, editor_id):
                for meth in ("onboard_to_bcn", "set_bcs_visibility"):
                    fn = getattr(prov, meth, None)
                    if fn is None:
                        continue
                    try:
                        await fn(bid)
                    except Exception as exc:  # noqa: BLE001 已就绪/不属于本用户 → 跳过
                        print(f"[provision] {meth}({bid}) 跳过:{exc!r}")
        finally:
            try:
                await prov._aclose()
            except Exception:  # noqa: BLE001
                pass
        print(f"[provision] writer_id={writer_id} editor_id={editor_id}")

        async with httpx.AsyncClient(timeout=300.0, headers=_HDRS) as cli:
            # 2) POST /api/v1/collaboration/tasks/execute → TaskService.execute → _run_yaml 成立 bcN 协作群
            r = await cli.post(f"{_BACKEND}/api/v1/collaboration/tasks/execute",
                               json=_execute_body(writer_id, editor_id))
            r.raise_for_status()
            body = r.json()
            data = body.get("data") or {}
            print(f"[execute] message={body.get('message')} data={data}")
            self.assertTrue(data.get("success"), f"execute 未成功:{data}")
            task_id = data.get("task_id")
            self.assertTrue(task_id, f"execute 响应缺 task_id:{data}")

            # 3) 轮询 dashboard(回调服务收到的执行进度落图)直到任务结束 / 超时
            g: dict = {}
            deadline = time.monotonic() + _TIMEOUT
            while time.monotonic() < deadline:
                pr = await cli.get(f"{_BACKEND}/api/v1/collaboration/tasks/dashboard",
                                   params={"task_id": task_id})
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
                        # extend_props 含 group_id / run_id(state_machine)/ session_id(chat);
                        # acceptance_result.verdict 看 PASS/FAIL。
                        "extend_props": ri.get("extend_props") or {},
                        "exec_error": ri.get("exec_error"),
                        "verdict": (ri.get("acceptance_result") or {}).get("verdict"),
                    })
                print(f"[snapshot] graph={g.get('status')} loop={g.get('loop_round')} nodes={snap}")
                if g.get("status") in ("DONE", "HUNG"):
                    break
                await asyncio.sleep(6.0)

        # 4) 输出 + 校验执行结果
        print(f"[final] graph={g.get('status')} tasks={len(g.get('tasks') or [])}")
        nodes = {t.get("node_id"): t for t in g.get("tasks") or []}
        root = nodes.get(task_id)
        for nid, nd in nodes.items():
            ri = nd.get("run_info") or {}
            print(f"  - {str(nid):28} {str(nd.get('status')):8} "
                  f"mode={ri.get('run_mode') or '-':11} "
                  f"assignee={str(ri.get('assignee') or '-')[:24]} "
                  f"verdict={(ri.get('acceptance_result') or {}).get('verdict')}")

        self.assertEqual(g.get("status"), "DONE",
                         f"全图未闭环 DONE:status={g.get('status')} (BCS 未自动运行状态机/未回投?)")
        self.assertIsNotNone(root, "根节点(task_id)未出现")
        self.assertEqual(root.get("status"), "DONE", "根未 DONE")
        ri = root.get("run_info") or {}
        self.assertEqual(ri.get("run_mode"), "coop_group", "根非 coop_group")
        # 群 id 前缀容忍两种后端:本地 stub/double 产 ``grp_<8hex>``;真 BCS(:21000)产 ``bcs_grp_<uuid>``。
        self.assertTrue(str(ri.get("assignee") or "").startswith(("grp_", "bcs_grp_")),
                        f"根 assignee 非群 id:{ri.get('assignee')!r}")
        acceptance = ri.get("acceptance_result") or {}
        self.assertEqual(acceptance.get("verdict"), "DONE",
                         f"根验收未 PASS:{acceptance} (BCS 回投 success=false?)")
        output = ri.get("output")
        self.assertTrue(output, "根无最终输出(output 为空)")
        print(f"[result] output={str(output)!r}")


if __name__ == "__main__":
    unittest.main()
