"""
Worker Config Model

存储于 bcsfuse_workers.config JSON 列，用于 Worker 行为配置。

当前字段：
- fusion_enable: 是否允许参与 Profile 融合，默认关闭需显式开启
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class WorkerConfig(BaseModel):
    """Worker 行为配置。fusion_enable 默认 False，需显式开启融合。"""

    fusion_enable: bool = Field(
        default=False,
        description="是否允许参与 Profile 融合。默认关闭，需显式开启",
    )

    model_config = {"extra": "allow"}  # Allow custom config fields


__all__ = ["WorkerConfig"]