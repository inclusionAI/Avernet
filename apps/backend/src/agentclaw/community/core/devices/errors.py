"""Device Errors — 设备领域异常定义.

统一异常来源，供 core 层和 api 层使用。
"""


class DeviceServiceError(Exception):
    """设备服务错误基类 — 供 DeviceService 及其子类使用。

    HTTP 设备路由统一 ``except DeviceServiceError`` 返回结构化错误，
    因此设备生命周期相关异常都挂在这棵树下。
    """
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class DeviceReleaseError(DeviceServiceError):
    """设备释放错误"""


class DeviceExecShellError(DeviceServiceError):
    """脚本执行错误"""


class DeviceAllocateError(Exception):
    """设备分配错误"""
    def __init__(self, message: str) -> None:
        super().__init__(message)


class AideskDirectoryError(DeviceAllocateError):
    """aidesk目录错误"""
    def __init__(self, message: str):
        super().__init__(message)


class ResourceInsufficientError(DeviceAllocateError):
    """资源不足错误"""
    def __init__(self, message: str):
        super().__init__(message)


class DeviceDomainError(Exception):
    """设备领域错误基类"""
    pass


class DeviceNotFoundError(DeviceDomainError):
    """设备不存在"""
    pass


class InvalidDeviceStatusError(DeviceDomainError):
    """设备状态无效"""
    pass


class DeviceAlreadyExistsError(DeviceDomainError):
    """单设备模式下已有设备"""
    pass


class DeviceLimitExceededError(DeviceDomainError):
    """超过设备数量上限"""
    pass
