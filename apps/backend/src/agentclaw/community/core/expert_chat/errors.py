"""
ExpertChat 业务异常定义
"""


class ExpertChatError(Exception):
    """ExpertChat 基础异常"""
    pass


class BotNotFoundError(ExpertChatError):
    """Bot 不存在"""
    pass


class BotNotActiveError(ExpertChatError):
    """Bot 状态不是 ACTIVE"""
    pass


class BotNotPublishedError(ExpertChatError):
    """Bot 未发布或发布版本不可用"""
    pass


class SessionCreateError(ExpertChatError):
    """创建 Session 失败"""

    def __init__(self, message: str, error_code: str = None, original_error: str = None):
        super().__init__(message)
        self.error_code = error_code
        self.original_error = original_error


class ConnectionError(ExpertChatError):
    """获取连接信息失败"""

    def __init__(self, message: str, error_code: str = None, original_error: str = None):
        super().__init__(message)
        self.error_code = error_code
        self.original_error = original_error


class ChatPermissionError(RuntimeError):
    """非 owner / 非 collaborator / 非 public bot — 不允许 chat。

    改造前(Task 2.5)藏在 ``device_service.get_device_connection_v2``
    内部副作用里(由 ``get_device_connection`` 的 4 分支权限校验抛出
    ``InvalidDeviceStatusError``);迁 ``DeviceContextResolver`` 后
    resolver 不查权限,本异常作为显式业务异常由 caller(ExpertChatService)
    在 ``_check_chat_access`` 中抛出。

    放行规则与旧 v2 等价:
    - bot owner 本人 → 放行
    - 非 owner 但 bot.public='1' → 放行
    - 协作者 (PermissionLevel.MEMBER 及以上) → 放行
    - 否则 → raise ChatPermissionError
    """
