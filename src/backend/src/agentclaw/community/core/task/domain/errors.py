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


class TrajectoryAnalysisNotConfiguredError(TaskError):
    """轨迹分析 bot 未配置(``TrajectoryAnalysisConfig.analysis_bot_id is None``)。

    REQ-8 + 决策 #10:``analysis_bot_id`` 是部署级配置(**非请求参数**),调用方不可选 bot。
    未配置(部署未在 ``task_trajectory.analysis_bot_id`` 填 bot_id)时,``do_analysis=true``
    无法分派 ``tc_bot`` 执行者 → 抛此错。P5b service 映射为 HTTP 503(服务能力未就绪,非 bot 调用失败
    ——与 ``TrajectoryAnalysisError`` 的 504 bot 超时区分:503 = 修配置,504 = bot 挂了/慢)。``do_analysis=false``
    纯读路径不触发此错(无需 bot)。不回填(本就没执行分析)。
    """


class TaskArtifactContentError(TaskError):
    """产物 manifest 内容形态非法(持久化的 kind-tagged content 出现未知分支/缺关键键)。

    读侧红线:``from_content_dict`` / ``artifact_from_dict`` 遇未知 ``kind`` 抛此错
    —— 库中脏值不得静默进领域(见 task_context/task_artifact/models.py 的不变量 2)。
    HTTP 面经 ``ENVELOPE_ERRORS`` 映射 500(存储行损坏属内部不变量破坏,非调用方可修复)。
    """


class TaskArtifactPublishError(TaskError):
    """产物双写(Artifact 发布)持久化失败。

    设计稿要求"持久化写入失败必须向上返回错误,不能吞掉后返回成功":集中化模式下
    fold 后 fire 的 manifest INSERT 失败抛此错(观测旁路决策 #14 的吞错豁免不适用
    —— 阶段二起 artifacts 是读侧依赖,静默缺行会令 attempt 序列 / supersedes 血缘
    链无法重建)。检索模式下例外降级 WARNING(见 spec 偏离记录:relay successor
    immutable,上抛后的重试 patch 会被不可变保护拒绝,形成收口残局)。
    HTTP 面经 ``ENVELOPE_ERRORS`` 映射 500(持久层故障)。
    """
