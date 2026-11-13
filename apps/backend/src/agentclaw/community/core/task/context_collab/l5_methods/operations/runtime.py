"""Method runtime helpers shared within L5 (single-tenant local store axes)."""

from datetime import datetime, timezone

from agentclaw.community.core.task.context_collab.persistence.method_store import (
    StoreAxes,
)
from agentclaw.community.utils.env_utils import get_current_env


def current_axes() -> StoreAxes:
    """服务层统一租户/环境轴(§6.2)。

    P1 单租户:tenant 与 ORM 表 ``avernet_tenant`` 的 server_default
    ("teamclaw")一致;P2 多租户扩展时在此处改为从调用方上下文派生。
    """
    return StoreAxes(avernet_tenant="teamclaw", env=get_current_env())


def _now() -> str:
    """UTC ISO 时间戳;store 层统一管理时间,不注入时钟。"""
    return datetime.now(timezone.utc).isoformat()


def _shape_of(data: dict, fields: tuple[str, ...]) -> tuple:
    """按字段清单抽取请求形态(幂等重放比对输入)。"""
    return tuple(data.get(field) for field in fields)
