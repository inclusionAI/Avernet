"""任务框架统一错误(全框架唯一 errors 收口点,对齐 README 内部层惯例)。"""
from __future__ import annotations


class TaskError(Exception):
    """任务框架基错。"""


class TaskStateError(TaskError):
    """非法状态流转(如 DONE→RUNNING、缺失 acceptance_result 翻态、违反 6 态机)。"""


class GraphIntegrityError(TaskError):
    """分解树完整性违反:单入防环/汇聚、结构父不存在、本批互为父子。"""


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
