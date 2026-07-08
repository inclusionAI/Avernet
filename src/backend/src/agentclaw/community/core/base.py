"""SQLAlchemy declarative base — single source of truth.

All ORM models managed by init_db() inherit from this Base.
This is the canonical definition. All other modules (plugins/, services/)
must import Base from here, not define their own declarative_base().

Known exceptions (intentional, self-managed lifecycle):
- core/devices/repository/sqlite_repo.py — LocalBase for device binding table

Architecture rule: core/ is the lowest layer with no upstream dependencies.
plugins/ depends on core/, not the other way around.
"""
from sqlalchemy.orm import declarative_base

Base = declarative_base()
