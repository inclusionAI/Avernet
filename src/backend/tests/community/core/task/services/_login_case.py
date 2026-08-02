"""共享真实需求 case:交付用户登录功能。

测试原则:任务与分解的**内容**用真实需求,只在**外部边界**(搜推 API / 分解器
LLM / 执行 skill 验收回投)上 mock。本模块集中放真实任务内容,供 tick 驱动
(``test_e2e_tick``)与图操作层(``test_graph_ops_e2e``)共用,避免占位字符串。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    AcceptanceCriteria,
    AcceptanceCriteriaKind,
    RunMode,
    SubTaskSpec,
    TaskGoal,
)

OBJECTIVE = "交付用户登录功能"

ACCEPTANCES = [
    AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "账号密码登录成功"}),
    AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "失败有明确错误提示"}),
    AcceptanceCriteria(kind=AcceptanceCriteriaKind.OUTPUT, properties={"label": "登录接口回归测试通过"}),
]


def goal() -> TaskGoal:
    return TaskGoal(objective=OBJECTIVE, acceptances=list(ACCEPTANCES))


# 顶层分解:3 个并行子任务(真实内容)
TOP_CHILDREN = [
    SubTaskSpec(node_id="s_design_api", spec="设计登录API契约(POST /login 入参/出参/错误码)", run_mode=RunMode.SINGLE_BOT),
    SubTaskSpec(node_id="s_impl_backend", spec="实现后端登录校验逻辑(密码哈希比对+失败计数锁定)", run_mode=RunMode.COOP_GROUP),
    SubTaskSpec(node_id="s_write_tests", spec="编写登录接口回归测试", run_mode=RunMode.SINGLE_BOT),
]

# “编写登录接口回归测试”搜推未匹配 → 再分解一层(真实内容)
TEST_CHILDREN = [
    SubTaskSpec(node_id="s3_setup_env", spec="搭建测试环境与mock用户数据", run_mode=RunMode.SINGLE_BOT),
    SubTaskSpec(node_id="s3_write_cases", spec="编写登录成功与失败用例脚本", run_mode=RunMode.SINGLE_BOT),
    SubTaskSpec(node_id="s3_wire_ci", spec="接入CI跑回归", run_mode=RunMode.SINGLE_BOT),
]

# 真实搜推映射:子任务 → 命中的执行方(BOT_SEARCH 与其 _disp 派发节点都要能搜到)。
# n_bot_search / s_write_tests 不在内 → 未匹配(触发分解)。
HIT_BOTS = {
    "s_design_api": ["arch-bot"], "s_design_api_disp": ["arch-bot"],
    "s_impl_backend": ["backend-group"], "s_impl_backend_disp": ["backend-group"],
    "s3_setup_env": ["test-bot"], "s3_setup_env_disp": ["test-bot"],
    "s3_write_cases": ["test-bot"], "s3_write_cases_disp": ["test-bot"],
    "s3_wire_ci": ["test-bot"], "s3_wire_ci_disp": ["test-bot"],
}