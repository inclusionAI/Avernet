"""PromptFormatterImpl 单测 —— 覆盖 skill 上报协议块、验收 prompt 与 RunnerContextBuilder 委派。

纯文本算子断言,不触网(零 case 约束:只消费 context dict + task_spec 字段)。
"""
from __future__ import annotations

from types import SimpleNamespace

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    Context,
    Goal,
    Metadata,
    RuntimeInfo,
    Status,
    TaskSpec,
)
from agentclaw.community.core.task.domain.prompt_constants import NO_WEB_SEARCH_CONSTRAINT
from agentclaw.community.core.task.task_runner.client.prompt_formatter import (
    PromptFormatterImpl,
    _RunnerContextBuilder,
    _skill_report_instruction,
)


def _node(instruction="做分析", objective="完成目标"):
    spec = TaskSpec(
        metadata=Metadata(task_id="t1", title="T", instruction=instruction),
        context=Context(background="bg"),
        goal=Goal(
            objective=objective,
            acceptances=[AcceptanceCriteria(id="ac1", description="验证项一")],
        ),
    )
    return SimpleNamespace(
        node_id="c1", task_id="t1", task_spec=spec, run_info=RuntimeInfo(assignee="b1")
    )


# ===== _skill_report_instruction =====
class TestSkillReportInstruction:
    def test_emits_reporter_identity_line_when_reporter_present(self):
        text = _skill_report_instruction(
            {
                "backend": "http://be",
                "reporter_bot_id": "b9",
                "reporter_role": "manager",
            },
            task_id="t1",
            node_id="c1",
        )
        assert "唯一上报者: reporter_bot_id=b9; reporter_role=manager。" in text
        assert "POST http://be/api/v1/collaboration/tasks/callback/report" in text
        assert '"task_id": "t1"' in text and '"node_id": "c1"' in text

    def test_defaults_reporter_line_and_backend_placeholder(self):
        text = _skill_report_instruction({}, task_id="t1", node_id="c1")
        assert "当前执行 Bot 是唯一上报者。" in text     # 无 reporter → 默认声明
        assert "任何成员都不得调用节点 callback" not in text  # 此块为单 bot 通道
        assert "POST {backend}/" in text                  # backend 缺省占位符
        # 自检约束 + 收尾轮约束 + 工具约束齐备(协议块的强制项)
        assert "上报前自检" in text
        assert "收尾轮约束" in text
        assert "exec/curl" in text


# ===== PromptFormatterImpl.format_verify =====
class TestFormatVerify:
    def test_verify_prompt_contains_acceptances_and_child_outputs(self):
        text = PromptFormatterImpl().format_verify(
            {
                "child_outputs": {"c2": "产出甲"},
                "acceptances": [
                    AcceptanceCriteria(id="ac1", description="甲"),
                    AcceptanceCriteria(id="ac2", description="乙"),
                ],
            },
            _node(),
        )
        assert text.startswith("验收标准:甲;乙")
        assert "子产出:{'c2': '产出甲'}" in text
        assert NO_WEB_SEARCH_CONSTRAINT in text

    def test_verify_prompt_defaults_on_empty_context(self):
        text = PromptFormatterImpl().format_verify({}, _node())
        assert text.startswith("验收标准:")               # 空 acceptances → 空串
        assert "子产出:{}" in text                        # 空 child_outputs → 空 dict


# ===== _RunnerContextBuilder =====
class TestRunnerContextBuilder:
    def test_build_delegates_to_runner_internal_context(self):
        runner = SimpleNamespace(
            _build_context=lambda task_id, node_id: {
                "task_id": task_id, "node_id": node_id, "mode": "delegated"
            }
        )
        ctx = _RunnerContextBuilder(runner).build("t9", "c9")  # integration 内聚访问
        assert ctx == {"task_id": "t9", "node_id": "c9", "mode": "delegated"}


# ===== PromptFormatterImpl.format_execute(回归锚:两条主路径不因补测回归)=====
class TestFormatExecuteRegression:
    def test_relay_node_returns_closure_without_callback_protocol(self):
        node = _node(instruction="# 接自 b0:上下文……\n## 本角色任务\n做接力")
        text = PromptFormatterImpl().format_execute({}, node)
        assert text.startswith("# 接自 b0")
        assert "回调地址" not in text and "POST" not in text  # 平台回收,无 Push 协议
        assert "执行闭环" in text                              # 接力三步闭环
        assert NO_WEB_SEARCH_CONSTRAINT not in text            # 不重复贴上报型约束

    def test_normal_node_returns_business_protocol(self):
        text = PromptFormatterImpl().format_execute({}, _node())
        assert "【业务节点执行协议】" in text
        assert "POST {backend}/api/v1/collaboration/tasks/callback/report" in text