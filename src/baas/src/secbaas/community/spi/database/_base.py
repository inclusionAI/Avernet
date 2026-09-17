"""Shared SQLAlchemy declarative base for ORM model definitions.

All ORM models MUST inherit from this Base — never create your own
declarative_base() instance. This ensures all models are registered
in the same metadata registry for DDL introspection and Alembic.

Usage:
    from secbaas.community.spi.database import Base

    class MyTable(Base):
        __tablename__ = "my_table"
        ...

Note: The distributed_lock.py module defines its own local Base.
Once models are migrated here, that local Base can be removed.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()

# BAAS owns DDL for these tables only. `ac_lock_table` is BAAS-exclusive;
# `ac_bots` / `ac_bot_publish` / `ac_entity_device_binding` are created by the
# AgentClaw backend despite BAAS reading (and for device_binding, writing) them.
BAAS_OWNED_TABLES = frozenset(
    {
        "ac_lock_table",
        "baas_api_key",
        "baas_bot",
        "baas_bot_device_rel",
        "baas_bot_qpm_config",
        "baas_bot_run",
        "baas_bot_run_interaction",
        "baas_bot_run_queue",
        "baas_bot_run_queue_chunk",
        "baas_bot_session",
        "baas_bot_ttl_renewal_schedule",
        "baas_device",
        "baas_device_template",
        "baas_file_transfer_tickets",
        "baas_local_user_machine",
        "baas_local_ws_relay_session",
        "baas_publish",
        "baas_publish_batch",
        "baas_publish_record",
        "baas_resource_key",
        "baas_resource_key_bot_mapping",
        "baas_session_file_tickets",
        "baas_system_config",
        "baas_tenant",
    }
)

# BAAS ORM modules that must be imported so their tables reach `Base.metadata`.
# Every module declaring a table in BAAS_OWNED_TABLES must appear here.
BAAS_ORM_MODULES = (
    "secbaas.community.core.repository.api_gateway._orm_model",
    "secbaas.community.core.repository.arca_ttl._orm_model",
    "secbaas.community.core.repository.bot._orm_model",
    "secbaas.community.core.repository.bot_device_rel._orm_model",
    "secbaas.community.core.repository.bot_qpm._orm_model",
    "secbaas.community.core.repository.bot_run._orm_model",
    "secbaas.community.core.repository.bot_run_interaction._orm_model",
    "secbaas.community.core.repository.bot_run_queue._orm_model",
    "secbaas.community.core.repository.bot_run_queue_chunk._orm_model",
    "secbaas.community.core.repository.bot_session._orm_model",
    "secbaas.community.core.repository.device._orm_model",
    "secbaas.community.core.repository.device_template._orm_model",
    "secbaas.community.core.repository.distributed_lock._orm_model",
    "secbaas.community.core.repository.file_transfer_ticket._orm_model",
    "secbaas.community.core.repository.local_user_machine._orm_model",
    "secbaas.community.core.repository.publish._orm_model",
    "secbaas.community.core.repository.publish_batch._orm_model",
    "secbaas.community.core.repository.publish_record._orm_model",
    "secbaas.community.core.repository.resource_key._orm_model",
    "secbaas.community.core.repository.session_file_ticket._orm_model",
    "secbaas.community.core.repository.system_config._orm_model",
    "secbaas.community.core.repository.tenant._orm_model",
    "secbaas.community.core.repository.ws_relay_session._orm_model",
)

# Created by the backend; BAAS only reads or joins them.
EXTERNALLY_OWNED_TABLES = frozenset(
    {
        "ac_bot_publish",
        "ac_bots",
        "ac_entity_device_binding",
    }
)
