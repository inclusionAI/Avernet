from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class BotSessionRecord:
    """Database record for baas_bot_session table.

    Columns per DDL schema (14 fields):
    id, gmt_create, gmt_modified, bot_uuid, invoker, session_id,
    req, result, err_msg, context, status, device_uuid, env, tenant

    Note: NO is_deleted column (immutable history per D-04).
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    bot_uuid: str
    invoker: str
    session_id: str
    req: dict[str, Any] | None
    result: dict[str, Any] | None
    err_msg: str | None
    context: dict[str, Any] | None
    status: str
    device_uuid: str
    env: str
    tenant: str
