"""
Local user machine repository for baas_local_user_machine table.

Implements ZDAS sync SQL pattern for Local PaaS user-machine tracking.
Supports heartbeat tracking, status management, and instance assignment.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from secbaas.community.logger import get_logger

log = get_logger("orm-repository")


@dataclass(slots=True)
class LocalUserMachineRecord:
    """Database record for baas_local_user_machine table.

    Columns per DDL schema:
    id, gmt_create, gmt_modified, template_id, user_id, machine_id, machine_info,
    last_heartbeat, connected_server_instance, status, env, connected_route_info
    """

    id: int
    gmt_create: datetime
    gmt_modified: datetime
    template_id: int
    user_id: str
    machine_id: str
    machine_info: dict[str, Any]
    last_heartbeat: datetime
    connected_server_instance: str
    status: str
    env: str
    connected_route_info: dict[str, Any] | None = None
