"""任务框架统一错误(全框架唯一 errors 收口点,对齐 README 内部层惯例)。"""
from __future__ import annotations


class TaskError(Exception):
    """任务框架基错。"""


class TaskStateError(TaskError):
    """非法状态流转(如 DONE→RUNNING、缺失 acceptance_result 翻态、违反 6 态机)。"""


class GraphIntegrityError(TaskError):
    """分解树完整性违反:单入防环/汇聚、结构父不存在、本批互为父子。"""


class GraphVersionConflictError(TaskError):
    """图基于过期版本写入时的乐观并发冲突。"""


class GraphAlreadyInitializedError(TaskError):
    """``initialize_graph`` 幂等冲突:同 task_id 已存在图。"""


class TaskNotFoundError(TaskError):
    """task_id 不存在。"""


class NodeNotFoundError(TaskError):
    """node_id 不存在。"""


class DispatchError(TaskError):
    """派发失败(搜推无 4 态匹配且非 BBS)。"""


class DecomposeError(TaskError):
    """分解产出违反硬契约(结构父未就绪/重复节点/本批互为父子)。"""


class BotIdentityResolutionError(TaskError):
    """产品 Bot ID 无法唯一解析为 BCS Bot UUID。"""


class TrajectoryAnalysisError(TaskError):
    """轨迹分析执行失败(``tc_bot`` 超时 / 调用失败 / 响应不可解析)。

    REQ-9 + 决策 #10:首期 ``do_analysis=true`` 同步带超时;bot 超时/失败时执行者**抛出此错**
    (决策 #14 的吞错豁免仅限观测旁路发射,不覆盖分析执行)。P5b service 把此错映射为
    HTTP 504 且**不**回填 ``analysis``(覆盖语义在失败时保护既有值)。
    """
