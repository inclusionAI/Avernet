"""Bot Health Checker 异常类

定义 BotHealthCheckerService 使用的异常体系。
"""


class BotHealthCheckerError(Exception):
    """BotHealthChecker 服务基类异常"""

    pass


class SandboxNotFoundError(BotHealthCheckerError):
    """沙箱不存在"""

    pass


class UnsupportedDeviceProviderError(BotHealthCheckerError):
    """不支持的设备提供方"""

    pass


class HealthCheckError(BotHealthCheckerError):
    """健康检查异常"""

    pass


class HealthCheckTimeoutError(HealthCheckError):
    """健康检查超时"""

    pass


class TTLExtendFailedError(BotHealthCheckerError):
    """TTL 续期失败"""

    pass


class PartialSuccessError(BotHealthCheckerError):
    """批量操作部分失败"""

    def __init__(self, success_count: int, failed_count: int, errors: list[str]):
        self.success_count = success_count
        self.failed_count = failed_count
        self.errors = errors
        super().__init__(
            f"Partial success: {success_count} succeeded, {failed_count} failed"
        )
