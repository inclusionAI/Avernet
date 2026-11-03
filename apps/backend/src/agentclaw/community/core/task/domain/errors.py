"""任务框架统一错误(全框架唯一 errors 收口点,对齐 README 内部层惯例)。"""
from __future__ import annotations


class TaskError(Exception):
    """任务框架基错。"""


class TaskStateError(TaskError):
    """非法状态流转(如 DONE→RUNNING、缺失 acceptance_result 翻态、违反 6 态机)。"""


class DispatchInProgress(TaskError):
    """S6 唯一投递意图已被其他执行者持有，可按 Retry-After 重试。"""


class TaskA2AStatusUnknownError(TaskError):
    """A2A Intake was accepted, but its durable Chat Run is still in progress."""


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
    """产物内容非法:未知 kind tag / 分支互斥破坏 / 路径逃逸 / 非 http(s) 链接 / 字段失配等。

    spec 2026-10-20-task-artifact-domain §3.4:内容分支校验 fail-loud,不静默丢弃、不猜分支;
    上报解析失败走既有回调错误信封(§4),写侧发布失败上抛回滚整个事务(§5.2)。
    """


class TaskArtifactStorageError(TaskError):
    """产物对象存储读写失败(put/get 返回 FAILED)。

    OBJECT_FILE(2026-10-26 修订,spec §1.3 例外)字节经后端流式代理进出对象存储;
    写失败时**不登记产修行**(单向失败安全:至多遗留无引用对象,决不出现引用悬空行),
    该错映射为 HTTP 502(存储不可用,非内容非法)。
    """


class TaskArtifactStorageNotConfiguredError(TaskError):
    """产物对象存储服务未接线(``TaskArtifactObjectStoragePlugin`` 未绑定)。

    映射为 HTTP 503:能力未就绪(修配置),与 ``TaskArtifactStorageError`` 的 502
    (存储挂了)及内容非法的 422 区分;仿 ``TrajectoryAnalysisNotConfiguredError`` 口径。
    """


class TaskArtifactObjectNotFoundError(TaskError):
    """artifact_id 未知,或产物行存在但其对象 key 在存储中 NOT_FOUND。

    下载口逐次校验:先按 artifact_id 查行,后按行上稳定 key 取字节;两段任一落空
    均为本错(HTTP 404)——与存储 FAILED 的 502 区分,与权限无关。
    """


class TaskArtifactUploadTooLargeError(TaskError):
    """上传字节超过 ``task_artifact_storage.max_upload_mb`` 配置上限。

    映射为 HTTP 413;分块/直传通道不在本期(后端代理 + 封顶,spec 修订记录)。
    """
