"""TaskExecutorBbsMixin 单测 —— 覆盖 BBS 旁路建群返回契约与 participant binding 解析。

纯内核算子(stub 掉 form_coop_group/get_group_session/notify 端口),不触网。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.modal_executor.task_executor_bbs import (
    TaskExecutorBbsMixin,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Host(TaskExecutorBbsMixin):
    """最小宿主:仅提供 mixin 需要的端口属性,便于断言调用。"""

    def __init__(self, *, session_id="s_1", groups=None):
        self.form_coop_group = AsyncMock(return_value="grp_1")
        self.get_group_session = AsyncMock(return_value=session_id)
        self.formed: list[GroupFormation] = []
        self._bcn = "bcn-stub"
        self._bot = "bot-stub"
        self._graph = "graph-stub"
        self._api_base_url = "http://backend"
        self._on_bbs_report = None
        self._bcs = AsyncMock()

        async def _capture_form(gf):
            self.formed.append(gf)
            return "grp_1"

        self.form_coop_group.side_effect = _capture_form


def _host(**kw) -> _Host:
    return _Host(**kw)


# ===== _bbs_execute_as_manager_worker_group =====
class TestBbsExecuteAsManagerWorkerGroup:
    def _execute(self, host, *, winner_bot_id="b1:U1", owner_user_id="U9", **kw):
        return _run(
            host._bbs_execute_as_manager_worker_group(
                task_id="t1",
                node_id="c0",
                winner_bot_id=winner_bot_id,
                owner_user_id=owner_user_id,
                task_instruction="执行尽调",
                deadline_monotonic=0.0,  # 不进入(已改为提前返回 path)轮询
                **kw,
            )
        )

    def test_builds_manager_worker_group_and_returns_session(self):
        host = _host()
        result = self._execute(host)
        assert result == {"session_id": "s_1", "success": True}  # 对齐 send_and_wait_async 形状
        gf = host.formed[0]
        assert gf.bot_ids == ["b1"]          # winner 'bot:owner' → driver bot 前缀
        assert gf.collab_mode == "manager_worker"
        assert gf.group_name == "t1-c0"
        assert gf.members_info == [{"bot_id": "b1", "role": "manager"}]
        assert gf.extend_props["owner_user_id"] == "U9"
        assert gf.extend_props["manager_bot_id"] == "b1"
        assert gf.extend_props["loop_task_id"] == "t1::c0"
        assert gf.extend_props["task_instruction"] == "执行尽调"

    def test_missing_driver_bot_raises_identity_error(self):
        host = _host()
        with pytest.raises(BotIdentityResolutionError):
            self._execute(host, winner_bot_id="")  # 无 driver bot → 拒绝建群
        host.form_coop_group.assert_not_awaited()

    def test_no_session_raises_timeout_for_fallback(self):
        host = _host(session_id="")
        with pytest.raises(asyncio.TimeoutError):
            self._execute(host)  # 群无 session → TimeoutError(由 bbs_runner 回退 send_and_wait_async)


# ===== run_bbs =====
class TestRunBbs:
    def test_notify_delegates_with_executor_seams(self, monkeypatch):
        from agentclaw.community.core.task.task_runner.modal_executor import (
            bbs_modal_executor,
        )

        captured = {}

        async def _fake_notify(*, execution_graph, bcn, bot, graph, backend_url,
                               skill_name, on_bbs_report, group_executor):
            captured.update(
                execution_graph=execution_graph, bcn=bcn, bot=bot, graph=graph,
                backend_url=backend_url, skill_name=skill_name,
                on_bbs_report=on_bbs_report, group_executor=group_executor,
            )

        monkeypatch.setattr(bbs_modal_executor, "notify", _fake_notify)
        monkeypatch.setattr(
            bbs_modal_executor, "_BBS_SKILL_NAME", "bbs_skill", raising=False
        )
        host = _host()
        _run(host.run_bbs(execution_graph="eg"))
        # 端口原样透传给 notify(委托 bbs_runner,不在此处改写语义)
        assert captured["execution_graph"] == "eg"
        assert captured["bcn"] == "bcn-stub"
        assert captured["bot"] == "bot-stub"
        assert captured["graph"] == "graph-stub"
        assert captured["backend_url"] == "http://backend"
        # 绑定方法:同一 func + 同一 __self__ 即等价(每次属性访问产生新的 bound 对象,不能用 is)
        assert captured["group_executor"] == host._bbs_execute_as_manager_worker_group


# ===== _state_machine_bindings =====
class TestStateMachineBindings:
    def test_explicit_mapping_with_source(self):
        gf = GroupFormation(
            bot_ids=[], collab_mode="state_machine",
            members_info=[],
            extend_props={
                "participant_bindings": {
                    "researcher": {"bot_ids": ["b1", "b2"], "source": "config"},
                    "writer": ["b3"],           # 非 dict spec → manual
                    "single_str": "b4",          # str → 单元素列表
                }
            },
        )
        bindings = TaskExecutorBbsMixin._state_machine_bindings(gf)
        assert bindings["researcher"] == {"source": "config", "bot_ids": ["b1", "b2"]}
        assert bindings["writer"] == {"source": "manual", "bot_ids": ["b3"]}
        assert bindings["single_str"] == {"source": "manual", "bot_ids": ["b4"]}

    def test_explicit_not_mapping_raises(self):
        gf = GroupFormation(
            bot_ids=[], collab_mode="state_machine", members_info=[],
            extend_props={"participant_bindings": ["researcher"]},
        )
        with pytest.raises(BotIdentityResolutionError):
            TaskExecutorBbsMixin._state_machine_bindings(gf)

    def test_explicit_empty_name_raises(self):
        gf = GroupFormation(
            bot_ids=[], collab_mode="state_machine", members_info=[],
            extend_props={"participant_bindings": {"  ": ["b1"]}},
        )
        with pytest.raises(BotIdentityResolutionError):
            TaskExecutorBbsMixin._state_machine_bindings(gf)

    def test_explicit_empty_bot_ids_raises(self):
        gf = GroupFormation(
            bot_ids=[], collab_mode="state_machine", members_info=[],
            extend_props={
                "participant_bindings": {
                    "researcher": {"bot_ids": [], "source": "config"},
                    "writer": [],
                }
            },
        )
        with pytest.raises(BotIdentityResolutionError):
            TaskExecutorBbsMixin._state_machine_bindings(gf)

    def test_members_info_fallback_aggregates_by_role(self):
        gf = GroupFormation(
            bot_ids=[], collab_mode="state_machine",
            members_info=[
                {"bot_id": "b1", "role": "researcher"},
                {"bot_id": "b2", "role": "researcher"},  # 同 role 聚合
                {"bot_id": "b3", "role": "writer"},
                "not-a-dict",                            # 非 dict 成员跳过
                {"role": "writer2"},                      # 缺 bot_id 跳过
                {"bot_id": "b4", "role": ""},             # 空 role 跳过
            ],
        )
        bindings = TaskExecutorBbsMixin._state_machine_bindings(gf)
        assert bindings == {
            "researcher": {"source": "manual", "bot_ids": ["b1", "b2"]},
            "writer": {"source": "manual", "bot_ids": ["b3"]},
        }

    def test_members_info_fallback_empty(self):
        gf = GroupFormation(bot_ids=[], collab_mode="state_machine", members_info=[])
        assert TaskExecutorBbsMixin._state_machine_bindings(gf) == {}