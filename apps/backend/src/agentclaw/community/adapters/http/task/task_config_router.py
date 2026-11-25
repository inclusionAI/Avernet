"""Internal ``/api/v1/collaboration/tasks/config`` sub-router (spec 2026-11-02).

Provides set (upsert) + match (typed read, returns only a bool) at the task
internal surface under ``get_current_user`` auth. The public surface mirrors
only match in ``adapters/http/openapi_v1/task/router.py``; both surfaces share
one ``TaskConfigServiceProtocol`` singleton via ``Injected(...)``.

Mirrors the task surface contract: ``response_model=Envelope[T]`` +
``@envelope_errors``; ``env`` resolved server-side from
``env_utils.get_current_env()``, never a request param — an env param would
let a caller cross-write pre into prod (the spec's worst anti-goal).

Request schemas do ``not`` validate shape per ``config_type``: pydantic accepts
the union ``str | bool | list[str]`` and the service does the typed
encode/validate. The pydantic ``Literal`` on ``config_type`` and the required
``value`` field give the 422 error paths the endpoint coverage gate expects.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.adapters.http.openapi_v1.contracts import Envelope
from agentclaw.community.adapters.http.openapi_v1.responses import (
    envelope,
    envelope_errors,
)
from agentclaw.community.api.task.task_config_service import TaskConfigServiceProtocol
from agentclaw.community.di import Injected
from agentclaw.community.utils import env_utils

router = APIRouter(prefix="/config", tags=["task-config"])


class TaskConfigSetRequest(BaseModel):
    """Set or upsert request. config_value shape is validated by the service
    per config_type; pydantic only accepts the union here."""

    config_name: str = Field(
        ..., description="The config name to write; a (config_name, env) row is upserted whole."
    )
    config_type: Literal["single_string", "bool", "set"] = Field(
        ...,
        description=(
            "How config_value is interpreted and how match compares: "
            "single_string is exact string equality, bool is boolean equality, "
            "set is membership of a string element."
        ),
    )
    config_value: str | bool | list[str] = Field(
        ...,
        description=(
            "The value to store (JSON-encoded by the service): a string for "
            "single_string, a bool for bool, a list of strings for set."
        ),
    )


class TaskConfigMatchRequest(BaseModel):
    """Match request. value is the single value to test; its expected shape is
    governed by the stored row's config_type at the service layer."""

    config_name: str = Field(
        ...,
        description="The config name to read; an unknown name returns matched false (not an error).",
    )
    value: str | bool = Field(
        ...,
        description=(
            "The single value to test: a string for single_string equality or "
            "set membership, a bool for bool equality."
        ),
    )


@router.post("/set", response_model=Envelope[dict])
@envelope_errors
async def set_task_config(
    body: TaskConfigSetRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskConfigServiceProtocol = Injected(TaskConfigServiceProtocol),  # noqa: B008
) -> Envelope[dict]:
    """整体覆盖写入一条 (config_name, env) 配置;返回 row id。"""
    env = env_utils.get_current_env()
    operator = user.operatorName or user.staffId
    config_id = service.set_config(
        config_name=body.config_name,
        config_type=body.config_type,
        config_value=body.config_value,
        env=env,
        operator=operator,
    )
    return envelope({"config_id": config_id}, request)


@router.post("/match", response_model=Envelope[dict])
@envelope_errors
async def match_task_config(
    body: TaskConfigMatchRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
    service: TaskConfigServiceProtocol = Injected(TaskConfigServiceProtocol),  # noqa: B008
) -> Envelope[dict]:
    """按存行的 config_type 判定 value 是否匹配;未知配置名 -> matched: False,
    只回布尔、不回存储值。"""
    env = env_utils.get_current_env()
    matched = service.match(
        config_name=body.config_name,
        value=body.value,
        env=env,
    )
    return envelope({"matched": matched}, request)
