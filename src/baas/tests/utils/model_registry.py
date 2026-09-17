"""Register every BAAS ORM model on ``Base.metadata``.

``Base.metadata`` is only populated once each ``_orm_model`` module is imported.
Tests build in-memory databases from that metadata, so they must import the full
model set — ``BAAS_ORM_MODULES`` — plus the read-only models for tables the
backend owns.
"""

from __future__ import annotations

from importlib import import_module

from secbaas.community.spi.database import BAAS_ORM_MODULES

_EXTERNAL_MODULES = (
    "secbaas.community.core.repository.ac_bot._orm_model",
    "secbaas.community.core.repository.ac_bot_publish._orm_model",
    "secbaas.community.core.repository.device_binding._orm_model",
)


def register_baas_models() -> None:
    """Import all BAAS ORM modules so their tables reach ``Base.metadata``."""
    for name in (*BAAS_ORM_MODULES, *_EXTERNAL_MODULES):
        import_module(name)
