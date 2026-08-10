"""Seed data required for the application to function.

These records match what E2E tests expect via DEFAULT_TENANT / DEFAULT_TEMPLATE_UUID.
Without them, the SQLite backend starts empty and all bot/device/tenant APIs fail.
"""

from __future__ import annotations

import json
from datetime import datetime
from hashlib import sha256

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

_SEED_TEMPLATE_LOCAL = {
    "id": 2,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "template_uuid": "TEMPLATE-f996ecc77d224ef7bd80757d8d2bcd0d",
    "tenant": "team_claw",
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "status": "ONLINE",
    "name": "local模板",
    "description": "Local sandbox device template",
    "config": json.dumps({"type": "LOCAL", "mng_offline_threshold_seconds": 30}),
    "template_id": 2,
    "type": "Local",
}

_SEED_TEMPLATE_POOLAB = {
    "id": 3,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "template_uuid": "TEMPLATE-54942a40aa794eaaae2be166f94890ed",
    "tenant": "team_claw",
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "status": "ONLINE",
    "name": "poolab模板",
    "description": "Poolab sandbox device template",
    "config": json.dumps(
        {
            "type": "POOLAB",
            "poolab_endpoint_pre": "http://poolab.example.com:8080",
            "poolab_tenant_id": "dummy_tenant",
            "poolab_tenant_token": "dummy_token",
        }
    ),
    "template_id": 3,
    "type": "Poolab",
}

_SEED_TEMPLATE_TECLAW = {
    "id": 4,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "template_uuid": "TEMPLATE-3106e731ffb04e0285e27c387e153737",
    "tenant": "team_claw",
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "status": "ONLINE",
    "name": "teclaw模板",
    "description": "TeClaw sandbox device template",
    "config": json.dumps(
        {"type": "TECLAW", "teclaw_endpoint": "http://dummy.com", "timeout": 30.0}
    ),
    "template_id": 4,
    "type": "TeClaw",
}

_SEED_TEMPLATE_SIGMA = {
    "id": 7,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "template_uuid": "TEMPLATE-a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6",
    "tenant": "team_claw",
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "status": "ONLINE",
    "name": "sigma模板",
    "description": "Sigma sandbox device template",
    "config": json.dumps(
        {
            "type": "Sigma",
            "endpoint": "https://sigma.example.com",
            "access_key": "dummy-access-key",
            "secret_key": "dummy-secret-key",
            "region": "default",
        }
    ),
    "template_id": 7,
    "type": "Sigma",
}

_SEED_TEMPLATE_K8S = {
    "id": 5,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "template_uuid": "TEMPLATE-8e4a2a3b4c5d4e6f7a8b9c0d1e2f3a4b",
    "tenant": "team_claw",
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "status": "ONLINE",
    "name": "k8s模板",
    "description": "K8s sandbox device template",
    "config": json.dumps(
        {
            "type": "K8s",
            "kubeconfig": "apiVersion: v1\nkind: Config\nclusters:\n- cluster:\n    server: https://stub-k8s:6443\n  name: stub\ncontexts:\n- context:\n    cluster: stub\n  name: stub\ncurrent-context: stub\n",
            "namespace": "default",
            "image": "test:latest",
        }
    ),
    "template_id": 5,
    "type": "K8S",
}

_SEED_TEMPLATE_DOCKER = {
    "id": 6,
    "gmt_create": datetime(2026, 1, 1, 0, 0, 0),
    "gmt_modified": datetime(2026, 1, 1, 0, 0, 0),
    "template_uuid": "TEMPLATE-9f5b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
    "tenant": "team_claw",
    "is_deleted": 0,
    "creator": "creator",
    "modifier": "modifier",
    "status": "ONLINE",
    "name": "docker模板",
    "description": "Docker sandbox device template",
    "config": json.dumps(
        {
            "type": "DOCKER",
            "image": "alpine:latest",
            "container_port": 8080,
            "memory_limit": "512m",
            "health_endpoint": "/health",
            "health_timeout_seconds": 120,
            "default_ttl_minutes": 1440,
        }
    ),
    "template_id": 6,
    "type": "Docker",
}

# This record is an internal local identity, not an HTTP bearer credential.
# BaaS's BCN downlink service looks it up only to build BotChatContext after
# the loopback bridge has already authenticated the request. Its prefix must
# match the singlebox BaaS configuration.
_SEED_BCN_API_KEY = {
    "api_key_hash": sha256(b"singlebox-bcn-internal-context").hexdigest(),
    "api_key_prefix": "9acXMLaU",
    "key_name": "singlebox-bcn-provider",
    "app_id": "singlebox-bcn-provider",
    "app_type": "bcn",
    "description": "Local BCS Provider downlink context",
    "rate_limit_rpm": None,
    "rate_limit_rpd": None,
    "status": "ACTIVE",
    "owner": "singlebox",
    "tenant": "team_claw",
    "env": "dev",
    "creator": "singlebox",
    "modifier": "singlebox",
    "policy": None,
}


def seed_sqlite(session: Session) -> None:
    """Insert required seed records into the SQLite database.

    The application depends on the team_claw tenant and its ARCA
    device template being present.  These are inserted after
    create_all() so that the in-memory SQLite backend is usable
    out of the box.
    """
    from secbaas.community.core.repository.api_gateway._orm_model import APIKeyModel
    from secbaas.community.core.repository.device_template import (
        DeviceTemplateModel,
    )
    from secbaas.community.core.repository.tenant import TenantModel
    from secbaas.community.logger import get_logger

    logger = get_logger("bootstrap")

    existing_tenant = (
        session.query(TenantModel).filter_by(id=_SEED_TENANT["id"]).first()
    )
    if existing_tenant is None:
        session.add(TenantModel(**_SEED_TENANT))
        logger.info("inserted seed tenant (team_claw)")

    existing_bcn_key = (
        session.query(APIKeyModel)
        .filter_by(api_key_prefix=_SEED_BCN_API_KEY["api_key_prefix"])
        .first()
    )
    if existing_bcn_key is None:
        session.add(APIKeyModel(**_SEED_BCN_API_KEY))
        logger.info("inserted local BCS Provider downlink context")

    for idx, seed in enumerate(
        [
            _SEED_TEMPLATE,
            _SEED_TEMPLATE_LOCAL,
            _SEED_TEMPLATE_POOLAB,
            _SEED_TEMPLATE_TECLAW,
            _SEED_TEMPLATE_SIGMA,
            _SEED_TEMPLATE_K8S,
            _SEED_TEMPLATE_DOCKER,
        ]
    ):
        existing_template = (
            session.query(DeviceTemplateModel).filter_by(id=seed["id"]).first()
        )
        if existing_template is None:
            session.add(DeviceTemplateModel(**seed))
            logger.info(
                f"inserted seed device template (id={seed['id']}, uuid={seed['template_uuid']})"
            )

    session.commit()
