"""SQLite external-table DDL adapts rowids without mutating shared models."""

from sqlalchemy import BigInteger, create_engine, inspect
from sqlalchemy.orm import Session

from secbaas.community.core.repository.device_binding._orm_model import (
    DeviceBindingModel,
)
from tests.utils.external_schema import create_external_tables


def test_sqlite_external_binding_insert_generates_id_without_mutating_model():
    engine = create_engine("sqlite://")
    try:
        original_type = DeviceBindingModel.__table__.c.id.type
        assert isinstance(original_type, BigInteger)
        create_external_tables(engine)
        create_external_tables(engine)  # safe when bootstrapped more than once
        assert DeviceBindingModel.__table__.c.id.type is original_type
        columns = inspect(engine).get_columns("ac_entity_device_binding")
        assert str(next(c["type"] for c in columns if c["name"] == "id")) == "INTEGER"
        with Session(engine) as session:
            row = DeviceBindingModel(
                entity_id="u1",
                entity_type="staff",
                device_id="d1",
                device_provider="arca",
                env="test",
                status="ACTIVE",
                applied_by="u1",
            )
            session.add(row)
            session.flush()
            assert row.id > 0
    finally:
        engine.dispose()
