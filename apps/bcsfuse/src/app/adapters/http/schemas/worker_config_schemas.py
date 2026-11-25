"""
Worker Config API Schemas

bcsfuse_workers.config 字段相关的 HTTP 请求/响应 DTO。

与 domain/models/worker_config.py 中的 WorkerConfig 领域模型不同，
这里只包含对外接口的数据结构。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SetWorkerConfigRequest(BaseModel):
    """修改 Worker 配置请求"""
    fusion_enable: bool = Field(..., description="是否允许参与 Profile 融合")


class WorkerConfigResponse(BaseModel):
    """Worker 配置响应"""
    success: bool
    worker_id: str
    fusion_enable: bool
    version: int


class WorkerConfigItem(BaseModel):
    """批量查询中单个 Worker 的配置项"""
    fusion_enable: bool


class BatchQueryConfigRequest(BaseModel):
    """批量查询 Worker 配置请求"""
    worker_ids: list[str] = Field(..., min_length=1, max_length=100, description="Worker ID 列表")


class BatchQueryConfigResponse(BaseModel):
    """批量查询 Worker 配置响应"""
    success: bool
    data: dict[str, WorkerConfigItem] = Field(default_factory=dict)
    not_found_ids: list[str] = Field(default_factory=list)


__all__ = [
    "SetWorkerConfigRequest",
    "WorkerConfigResponse",
    "WorkerConfigItem",
    "BatchQueryConfigRequest",
    "BatchQueryConfigResponse",
]