"""Schemas for the task result writeback endpoint."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TaskResultWritebackRequestDTO(BaseModel):
    """回写请求:task_id + 最终结果消息原文(不改任务态,只送回发起会话)。"""

    task_id: str = Field(min_length=1)
    message: str = Field(min_length=1)