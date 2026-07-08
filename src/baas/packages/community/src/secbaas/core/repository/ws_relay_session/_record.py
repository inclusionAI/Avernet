from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class WsRelaySessionRecord:
    """Database record for baas_local_ws_relay_session table.

    Columns per DDL schema (11 fields):
    id, gmt_create, gmt_modified, session_id, machine_id,
    connected_server_instance, status, env, gmt_close,
    connected_route_info, operator

    Note: connected_route_info is dict (deserialized from JSON string in Model).
    gmt_close is datetime | None (NULL in init/active rows).
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    session_id: str
    machine_id: str
    connected_server_instance: str
    status: str
    env: str
    gmt_close: datetime | None
    connected_route_info: dict[str, Any] | None
    operator: str
