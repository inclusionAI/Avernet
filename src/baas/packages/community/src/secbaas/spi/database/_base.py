"""Shared SQLAlchemy declarative base for ORM model definitions.

All ORM models MUST inherit from this Base — never create your own
declarative_base() instance. This ensures all models are registered
in the same metadata registry for DDL introspection and Alembic.

Usage:
    from secbaas.spi.database import Base

    class MyTable(Base):
        __tablename__ = "my_table"
        ...

Note: The distributed_lock.py module defines its own local Base.
Once models are migrated here, that local Base can be removed.
"""

from sqlalchemy.orm import declarative_base

Base = declarative_base()