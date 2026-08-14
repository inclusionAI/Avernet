"""TaskGraphService:任务图谱 SSOT + 原子变更唯一网关(7+2 API)。

对齐 plan §3.1 + 任务图谱文档 lunk1txfuv6gtwk2。
"""
from __future__ import annotations

from agentclaw.community.core.task.domain.models import (
    NodeOpResult,
    TaskExecutionGraph,
    TaskInfo,
    TaskNode,
    TaskNodePatch,
    TaskNodeQueryCriteria,
)


class TaskGraphService:
    """任务图谱 SSOT + 原子变更唯一网关。

    边界:只做图结构 + 节点/图级状态原子写 + 派生只读查询;不含编排(不调编排核、不搜推、不规划)。
    结构归属由 relations 分解树(单入)表达;depth/结构子/结构父均从 relations 派生。
    """

    def initialize_graph(self, task_info: TaskInfo) -> TaskExecutionGraph:
        """建图首帧(全局 RUNNING,只含根节点 PENDING,task_id=task_spec.metadata.id,run_id 分配);
        幂等:同 task_id 重复调抛 GraphAlreadyInitializedError。调用方:execute 内部。"""
        raise NotImplementedError

    def add_task_nodes(self, tasks: list[TaskNode]) -> TaskExecutionGraph:
        """并子图(单写 relations 分解树)。触发条件(由编排核判后调):a 根 PENDING 初始规划 /
        b 叶子 FAILED+gaps 补救 / c 父 PLANNING ∧ 无 RUNNING 下一层规划。登记分解树:每新子挂同一
        结构父写入 relations DEPENDENCY 边(src=结构父,dst=新子,单入);父进 PLANNING。task_id 从
        tasks[0].task_id 取。返回更新后的整图。"""
        raise NotImplementedError

    def update_task_node_info(self, patch: TaskNodePatch) -> NodeOpResult:
        """节点级原子状态流转网关。唯一翻态依据=patch.acceptance_result:
        PASS→DONE / FAIL+gaps→FAILED(验收 skill 强制要求给 gaps,不存在 FAIL 无 gaps);
        无 acceptance_result 只 fold output 不翻态(Harness 复位用 patch.status=PENDING 回退)。
        派发写:patch.run_mode(str)/assignee 落库 + 置 RUNNING。task_id/node_id 从 patch 内取;幂等。"""
        raise NotImplementedError

    def query_task_dashboard(
        self, task_id: str, node_id: str | None = None
    ) -> TaskExecutionGraph:
        """只读看板快照(整图或按 node_id 子树投影)。调用方:API(经 facade get_task_dashboard)。"""
        raise NotImplementedError

    def query_task_nodes(
        self, task_id: str, criteria: TaskNodeQueryCriteria
    ) -> list[TaskNode]:
        """按条件查节点。就绪扫描:criteria={status=PENDING}→ 返回 PENDING 可派发节点
        (PLANNING 委托态不在 PENDING,天然排除);has_child_tasks 可筛叶/内部节点。"""
        raise NotImplementedError

    def get_child_tasks(self, task_id: str, node_id: str) -> list[TaskNode]:
        """读某节点【结构子】=relations 中 src_id==node_id 的 dst 节点(直接分解产物)。
        用途:验收时机/验收上下文聚合/传播判定(决策C:本批兄弟全DONE)/规划去重。"""
        raise NotImplementedError

    def get_parent_task(self, task_id: str, node_id: str) -> TaskNode | None:
        """读某节点【结构父】=relations 中 dst_id==node_id 的 src 节点(单入,至多 1 个;根返回 None)。
        用途:执行上下文聚合结构父 P 的聚合上下文、深度闸门递归上溯、定位兄弟。"""
        raise NotImplementedError

    def remove_subtree(self, task_id: str, node_id: str) -> TaskExecutionGraph:
        """删节点 + 其下整个子树(递归 get_child_tasks 删;含 relations 边)。
        触发:升 BBS 时——某 xx_node 搜推 MISS 且其下所有子都 MISS、没走 RUNNING(整子树无效)。"""
        raise NotImplementedError

    def _node_depth(self, task_id: str, node_id: str) -> int:
        """从 relations 分解树递归自算深度(派生不持久)。内层深度闸门(MAX_DEPTH,升 BBS 阈值)读。"""
        raise NotImplementedError

    def _execution_config(self, task_id: str) -> dict:
        """读 MAX_DEPTH(内层升 BBS 阈值)/ BBS_MAX_DEPTH(外层 STUCK 阈值,默认 3)等。"""
        raise NotImplementedError
