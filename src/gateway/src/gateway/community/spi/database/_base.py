"""Shared SQLAlchemy declarative base for ORM model definitions.

All ORM models MUST inherit from this Base — never create your own
declarative_base() instance. This ensures all models are registered
in the same metadata registry for DDL introspection and Alembic.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()
