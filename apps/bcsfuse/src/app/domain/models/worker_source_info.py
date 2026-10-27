"""
Worker Source Info

Worker 来源信息模型。
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class WorkerSourceType(str, Enum):
    """
    Worker 来源类型

    - API: 通过 API 注册（未来主路径）
    - FILE: 通过文件导入（兼容路径）
    - IMPORT: 批量导入
    """
    API = "api"
    FILE = "file"
    IMPORT = "import"


class WorkerSourceInfo(BaseModel):
    """
    Worker 来源信息

    Attributes:
        source_type: 来源类型
        source_ref: 来源引用（文件路径 / API 来源标识）
        external_id: 外部 ID（上游系统的 worker 标识）
        imported_at: 导入时间
    """

    source_type: WorkerSourceType = Field(
        default=WorkerSourceType.API,
        description="来源类型"
    )
    source_ref: Optional[str] = Field(
        None,
        description="来源引用"
    )
    external_id: Optional[str] = Field(
        None,
        description="外部 ID"
    )
    imported_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="导入时间"
    )

    model_config = {
        "extra": "forbid",
    }


__all__ = ["WorkerSourceType", "WorkerSourceInfo"]