"""Provision the AgentClaw-owned tables BAAS reads or joins.

In production the backend creates ``ac_bots``, ``ac_bot_publish`` and
``ac_entity_device_binding``; BAAS only queries them. Test processes run BAAS
alone, so nothing would create those tables. ``create_external_tables`` mirrors
the backend's DDL by creating them from BAAS's read-only models, which carry the
column layout BAAS expects.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from secbaas.community.spi.database import EXTERNALLY_OWNED_TABLES, Base
from tests.utils.model_registry import register_baas_models


def create_external_tables(engine: Engine) -> None:
    """Create the externally-owned tables on ``engine``, if absent."""
    register_baas_models()
    tables = [
        t for t in Base.metadata.sorted_tables if t.name in EXTERNALLY_OWNED_TABLES
    ]
    Base.metadata.create_all(engine, tables=tables, checkfirst=True)
