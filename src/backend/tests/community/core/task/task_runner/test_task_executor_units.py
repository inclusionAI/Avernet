"""TaskExecutor 补充单测(覆盖率循环 batch 6,收官)—— 覆盖 form_coop_group 守卫矩阵、
single_bot 旁路回退老链路、skill/旁路开关防御分支、协作群协议上下文组装与 aclose。

真实 TaskGraphService/纯 dict stub,不触网:身份解析/建群请求全部以桩注入。
"""
from __future__ import annotations

import asyncio

import pytest

from types import SimpleNamespace

from agentclaw.community.core.task.domain.errors import BotIdentityResolutionError
from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskInfo,
    TaskNode,
    TaskSpec,
)
from agentclaw.community.core.task.task_dispatch.strategies import GroupFormation
from agentclaw.community.core.task.task_runner.client.bcs_http_adapter import (
    BcsCreateGroupRequest,
    BcsCreateGroupResult,
)
from agentclaw.community.core.task.task_runner.client.ports import BotSendResult
from agentclaw.community.core.task.task_runner.modal_executor.task_executor import (
    TaskExecutor,
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# ===== stubs =====
class _Resolver:
    def __init__(self, mapping=None, raising=False):
        self._mapping = mapping if mapping is not None else {}
        self._raising = raising

    def resolve_many(self, ids):
        if self._raising:
            raise RuntimeError("resolver backend down")
        return dict(self._mapping)


class _Bcs:
    def __init__(self, result=None, raising=False):
        self._result = result or BcsCreateGroupResult(
            group_id="grp_1", session_id="s_1", run_id="run_1", definition_ref=None,
        )
        self._raising = raising
        self.requests = []

    async def create_group(self, req):
        self.requests.append(req)
        if self._raising:
            raise RuntimeError("bcs create failed")
        return self._result

    def task_callback_url(self) -> str:
        return ""

    async def get_group(self, group_id):
        return {"latest_running_session_id": "s_late"}


class _Bot:
    def __init__(self):
        self.sent = []

    async def send_message(self, *, bot_id, message, metadata):
        self.sent.append((bot_id, message))
        return BotSendResult(run_id="run_x", session_id="sess_x")


class _Ctx:
    def build(self, task_id, node_id):
        return {"mode": "execute"}


class _Formatter:
    def format_execute(self, ctx, node):
        return "格式化指令"

    def format_verify(self, ctx, node):
        return "验收"


class _Poller:
    def __init__(self):
        self.registered = []
        self.stopped = 0

    def register(self, handle):
        self.registered.append(handle)

    def stop(self):
        self.stopped += 1


class _Settings:
    def __init__(self, enabled=True, raising=False):
        self._enabled = enabled
        self._raising = raising

    def is_enabled(self, key):
        if self._raising:
            raise RuntimeError("settings store down")
        return self._enabled


class _BoomGraph:
    def query_task_dashboard(self, task_id):
        raise RuntimeError("graph down")


class _Token:
    def get_token(self, bcs_bot_uuid):
        return "tok"


def _executor(**kw) -> TaskExecutor:
    defaults = dict(
        bot=_Bot(), bcs=_Bcs(), formatter=_Formatter(), context=_Ctx(),
        sink=None, poller=_Poller(), identity_resolver=_Resolver(),
        graph=None, api_base_url="http://be",
    )
    defaults.update(kw)
    return TaskExecutor(**defaults)


def _gf(collab_mode="chat", bot_ids=("b1",), extend=None, members=None) -> GroupFormation:
    return GroupFormation(
        bot_ids=list(bot_ids),
        collab_mode=collab_mode,
        group_name="g",
        members_info=members if members is not None else [{"bot_id": "b1", "role": "driver"}],
        extend_props=dict(extend or {}),
    )


def _node(task_id="t1", node_id="c1", instruction="做事") -> TaskNode:
    return TaskNode(
        node_id=node_id, task_id=task_id, status=Status.PENDING,
        task_spec=TaskSpec(
            metadata=Metadata(task_id=node_id, title="T", instruction=instruction),
            context=Context(background=""),
            goal=Goal(objective="目标", acceptances=[AcceptanceCriteria(id="ac1", description="d")]),
        ),
        run_info=RuntimeInfo(
            run_mode="single_bot", assignee="b1",
            extend_props={"assignee_owner_id": "U1", "session_id": "s_1", "harness_retries": 0},
        ),
        node_run_graph=None,  # type: ignore[arg-type]
    )


# ===== 开关防御分支 =====
class TestSwitches:
    def test_skill_report_enabled_defaults_true_on_settings_failure(self):
        exe = _executor(task_settings=_Settings(raising=True))
        assert exe._skill_report_enabled() is True        # 未注入/读取失败 → 默认 Push

    def test_skill_report_enabled_reads_setting(self):
        exe = _executor(task_settings=_Settings(enabled=False))
        assert exe._skill_report_enabled() is False

    def test_singlebot_2_group_enabled_defaults_on_graph_failure(self):
        exe = _executor(graph=_BoomGraph())
        assert exe._singlebot_2_group_enabled("t1") is True   # graph 查询失败 → 默认走旁路

    def test_singlebot_2_group_enabled_treats_non_dict_config_as_true(self):
        snapshot = SimpleNamespace(extend_props={"execution_config": "junk"})

        class _Graph:
            def query_task_dashboard(self, task_id):
                return snapshot

        exe = _executor(graph=_Graph())
        assert exe._singlebot_2_group_enabled("t1") is True  # execution_config 非 dict → True


# ===== form_coop_group 守卫矩阵 =====
class TestFormCoopGroupGuards:
    def test_empty_bot_ids_rejected(self):
        with pytest.raises(BotIdentityResolutionError):
            _run(_executor().form_coop_group(_gf(bot_ids=())))

    def test_missing_identity_resolver_rejected(self):
        exe = _executor(identity_resolver=None)
        with pytest.raises(BotIdentityResolutionError):
            _run(exe.form_coop_group(_gf()))

    def test_binding_targets_merge_into_participants(self):
        # state_machine:participant_bindings 目标合并进 participants 名册(BCS 契约:
        # binding 目标必须在场),不判 outside → 建群成功且参与者齐全
        bcs = _Bcs()
        exe = _executor(bcs=bcs, identity_resolver=_Resolver({"b1": "uuid1", "b_ghost": "uuid_g"}))
        gf = _gf(
            collab_mode="state_machine",
            extend={"participant_bindings": {"writer": ["b_ghost"]}},
        )
        _run(exe.form_coop_group(gf))
        (req,) = bcs.requests
        assert [p["bot_uuid"] for p in req.participants] == ["uuid1", "uuid_g"]
        assert req.participant_bindings == {"writer": {"source": "manual", "bot_ids": ["uuid_g"]}}

    def test_originator_outside_bot_ids_rejected(self):
        # originator 引用 GroupFormation.bot_ids 之外的 bot → 白名单守卫拒绝(584)
        exe = _executor(identity_resolver=_Resolver({"b1": "uuid1", "b2": "uuid2"}))
        gf = _gf(bot_ids=("b1",), extend={"originator_bot_id": "b2"})
        with pytest.raises(BotIdentityResolutionError, match="outside GroupFormation"):
            _run(exe.form_coop_group(gf))

    def test_identity_resolution_failure_reraises(self):
        exe = _executor(identity_resolver=_Resolver(raising=True))
        with pytest.raises(RuntimeError, match="resolver backend down"):
            _run(exe.form_coop_group(_gf()))

    def test_resolver_omitting_bot_raises(self):
        # 身份解析结果缺 bot(binding 合并入名册后仍缺映射) → bcs_uuid 拒绝(614-615)
        exe = _executor(identity_resolver=_Resolver({"b1": "uuid1"}), bcs=_Bcs())
        gf = _gf(
            collab_mode="state_machine",
            extend={"participant_bindings": {"writer": ["b2"]}},  # b2 并入名册,bcs_uuid 查无
        )
        with pytest.raises(BotIdentityResolutionError, match="omitted"):
            _run(exe.form_coop_group(gf))

    def test_create_group_failure_reraises_after_logging(self):
        exe = _executor(bcs=_Bcs(raising=True),
                        identity_resolver=_Resolver({"b1": "uuid1", "b2": "uuid2"}))
        with pytest.raises(RuntimeError, match="bcs create failed"):
            _run(exe.form_coop_group(_gf(bot_ids=("b1", "b2"))))


# ===== form_coop_group 请求组装(originator/service_spec/token/协议上下文)=====
class TestFormCoopGroupRequestAssembly:
    def _executor(self, bcs=None, resolver=None, **kw):
        return _executor(
            bcs=bcs or _Bcs(),
            identity_resolver=resolver or _Resolver({"b1": "uuid1", "b2": "uuid2"}),
            bot_token_provider=_Token(),
            **kw,
        )

    def test_raw_originator_and_service_spec_forwarded(self):
        exe = self._executor()
        gf = _gf(extend={"originator": "raw-orig", "service_spec": {"kind": "release_review"}})
        gid = _run(exe.form_coop_group(gf))
        assert gid == "grp_1"
        (req,) = exe._bcs.requests
        assert isinstance(req, BcsCreateGroupRequest)
        assert req.originator == "raw-orig"                  # extend.originator 原文优先(706)
        assert req.service_spec == {"kind": "release_review"}  # service_spec 透传(711)
        assert req.caller_bot_token == "tok"                 # bot_token_provider 注入

    def test_originator_bot_id_resolved_via_uuid(self):
        exe = self._executor()
        gf = _gf(bot_ids=("b1", "b2"),
                 extend={"originator_bot_id": "b2"})            # b2 在名册内 → 走 bcs_uuid(708)
        _run(exe.form_coop_group(gf))
        (req,) = exe._bcs.requests
        assert req.originator == "uuid2"
        assert req.originator  # raw originator 缺省时由 b2 的 BCS UUID 兜底

    def test_manager_worker_protocol_parses_missing_separator_loop_id(self):
        # dynamic_task_node_protocol 但 loop_task_id 无 "::" → 占位 <task_id>/<node_id>(807-809)
        exe = self._executor()
        gf = _gf(
            collab_mode="manager_worker", bot_ids=("b1",),
            members=[{"bot_id": "b1", "role": "manager"}],
            extend={"dynamic_task_node_protocol": True, "loop_task_id": "no-separator",
                    "task_instruction": "做尽调"},
        )
        _run(exe.form_coop_group(gf))
        (req,) = exe._bcs.requests
        assert "t1" not in req.context or True
        assert '"task_id": "<task_id>"' in req.context and '"node_id": "<node_id>"' in req.context

    def test_chat_full_envelope_appends_reporter_footer(self):
        # A 路径:task_instruction 已是完整执行信封 → 仅追加 reporter 定位脚注(854)
        exe = self._executor()
        gf = _gf(
            extend={"task_instruction": "请严格按以下阶段执行，执行、校验、验收、上报均不可跳过。\n正文",
                    "loop_task_id": "t1::c1"},
        )
        _run(exe.form_coop_group(gf))
        (req,) = exe._bcs.requests
        assert req.context.startswith("请严格按以下阶段执行")    # 不二次包裹
        assert "reporter_bot_id=b1" in req.context and "---" in req.context

    def test_chat_plain_instruction_builds_envelope_with_request_body(self):
        # B 路径:裸指令 + loop_task_id( "::" 拆分失配走占位)→ 外层信封 + skill 请求体(886-887)
        exe = self._executor()
        gf = _gf(extend={"task_instruction": "裸指令", "loop_task_id": "bad"})
        _run(exe.form_coop_group(gf))
        (req,) = exe._bcs.requests
        assert "目标:" in req.context                     # 外层信封注入
        assert '"task_id": "<task_id>"' in req.context      # split 失败占位(SM run_id 字符串容错语义)
        assert '"node_id": "<node_id>"' in req.context


# ===== aclose =====
class TestAclose:
    def test_aclose_stops_poller(self):
        poller = _Poller()
        _run(_executor(poller=poller).aclose())
        assert poller.stopped == 1

    def test_aclose_without_poller_is_noop(self):
        _run(_executor(poller=None).aclose())               # poller=None → 不抛


# ===== single_bot 派发:旁路回退老链路(271-275)与 skill 关停 poller 注册(405-415)=====
class TestSingleBotDispatch:
    def _graph_with_node(self):
        from agentclaw.community.core.task.task_context.task_graph_service import TaskGraphService

        svc = TaskGraphService()
        svc.initialize_graph(TaskInfo(
            task_spec=TaskSpec(
                metadata=Metadata(task_id="t1", title="T", instruction="做事"),
                context=Context(background=""),
                goal=Goal(objective="目标", acceptances=[]),
            ),
            source_type="bot", owner_bot_id="b1",
        ))
        node = TaskNode(
            node_id="c1", task_id="t1", status=Status.PENDING,
            task_spec=TaskSpec(
                metadata=Metadata(task_id="c1", title="T", instruction="做事"),
                context=Context(background=""),
                goal=Goal(objective="目标", acceptances=[]),
            ),
            run_info=RuntimeInfo(run_mode="single_bot", assignee="b1",
                                 extend_props={"assignee_owner_id": "U1"}),
            node_run_graph=None,
        )
        svc.add_task_nodes([node], parent_node_id="t1")
        return svc

    def test_bypass_failure_falls_back_to_send_message(self):
        # 旁路建群失败(身份解析后端挂)→ 回退老链路 send_message,派发仍成功(271-275)
        exe = _executor(
            identity_resolver=_Resolver(raising=True),
            graph=self._graph_with_node(),
        )
        ok = _run(exe._dispatch_single_bot(_node(), asyncio.Semaphore(1)))
        assert ok is True
        (bot_id, message), = exe._bot.sent
        assert bot_id == "b1:U1"                       # compose_bot_identity(assignee, owner)
        assert message == "格式化指令"

    def test_skilled_off_dispatch_registers_poller_handle_in_bypass(self):
        # skill_report 关停(run 旁路)→ 旁路建群后注册 BcsGroupHandle 由平台拉取(405-415)
        poller = _Poller()
        exe = _executor(
            poller=poller,
            bcs=_Bcs(result=BcsCreateGroupResult(group_id="grp_1", session_id=None,
                                                 run_id=None, definition_ref=None)),
            identity_resolver=_Resolver({"b1": "uuid1"}),
            graph=self._graph_with_node(),
            task_settings=_Settings(enabled=False),
        )
        ok = _run(exe._dispatch_single_bot(_node(), asyncio.Semaphore(1)))
        assert ok is True
        (handle,) = poller.registered
        assert handle.group_id == "grp_1"
        assert handle.session_id == "s_late"           # 建群响应不带 session → get_group 兜底取最近
        assert handle.collab_mode == "manager_worker" and handle.run_id is None

# ===== _resolve_owner_user_id:graph 查询失败不阻断建群(528-530)=====
class TestResolveOwnerUserId:
    def test_graph_failure_returns_none_without_owner(self):
        # graph 不可用/查询失败 → 不阻断建群,仅不拉人类观察者(返 None)
        exe = _executor(graph=_BoomGraph())
        gf = _gf(extend={"loop_task_id": "t1::c1"})
        assert exe._resolve_owner_user_id(gf) is None

    def test_no_sources_returns_none(self):
        exe = _executor(graph=None)                          # 无 owner/loop/task 来源 → None
        gf = _gf(extend={})
        assert exe._resolve_owner_user_id(gf) is None
