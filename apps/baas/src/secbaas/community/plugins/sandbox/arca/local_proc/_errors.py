class DeviceAllocateError(Exception):
    """设备分配错误"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
