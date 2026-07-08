"""Seed data required for the application to function.

These records match what E2E tests expect via DEFAULT_TENANT / DEFAULT_TEMPLATE_UUID.
Without them, the SQLite backend starts empty and all bot/device/tenant APIs fail.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

_SEED_TENANT = {
    "id": 1,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "name": "team_claw",
    "description": "team claw",
    "extra_config": json.dumps(
        {"default_template_uuid": "2d0781db473f4883a77b6b849b1a1422"}
    ),
    "env": "dev",
}

_SEED_TEMPLATE = {
    "id": 1,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "template_uuid": "TEMPLATE-4d0e2849d7004111836333de782b95d8",
    "tenant": "team_claw",
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "status": "ONLINE",
    "name": "arca模板0",
    "description": "测试update",
    "config": json.dumps(
        {
            "type": "ARCA",
            "base_url": "http://arca.example.com:8080",
            "api_key": "dummy_api_key",
            "app_name": "secbaas",
            "template_id": "ARCA-TEMPLATE-0000000095b3fe16",
            "oss_mount_id": "dummy_mount_id",
            "default_ttl_minutes": 1440,
            "timeout": 30.0,
        }
    ),
    "template_id": 0,
    "type": "ARCA",
}


def seed_sqlite(session: Session) -> None:
    """Insert required seed records into the SQLite database.

    The application depends on the team_claw tenant and its ARCA
    device template being present.  These are inserted after
    create_all() so that the in-memory SQLite backend is usable
    out of the box.
    """
    from secbaas.core.repository.device_template import (
        DeviceTemplateModel,
    )
    from secbaas.core.repository.tenant import TenantModel
    from secbaas.logger import get_logger

    logger = get_logger("bootstrap")

    existing_tenant = (
        session.query(TenantModel).filter_by(id=_SEED_TENANT["id"]).first()
    )
    if existing_tenant is None:
        session.add(TenantModel(**_SEED_TENANT))
        logger.info("inserted seed tenant (team_claw)")

    existing_template = (
        session.query(DeviceTemplateModel).filter_by(id=_SEED_TEMPLATE["id"]).first()
    )
    if existing_template is None:
        session.add(DeviceTemplateModel(**_SEED_TEMPLATE))
        logger.info(
            "inserted seed device template (TEMPLATE-4d0e2849d7004111836333de782b95d8)"
        )

    session.commit()
