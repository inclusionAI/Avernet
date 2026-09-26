"""
代理相关的 Pydantic 模型定义
"""

from pydantic import BaseModel, Field


class ProxyExecRequest(BaseModel):
    """代理执行命令请求"""

    sandbox_id: str = Field(..., description="沙箱ID")
    command: str = Field(..., description="执行的命令")


class ProxyHealthRequest(BaseModel):
    """代理健康检查请求"""

    sandbox_id: str = Field(..., description="沙箱ID")
