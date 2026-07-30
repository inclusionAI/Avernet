"""Smoke tests for the tenant master table (``avernet_tenant``) — ORM-only."""

from __future__ import annotations

from sqlalchemy import select

from gateway.community.bootstrap._authn import build_database
from gateway.community.core.tenant import TenantRow


async def test_tenant_row_table_creates_and_round_trips() -> None:
    db = build_database()
    with db.orm_session() as session:
        session.add(
            TenantRow(name="t-1", description="demo", owner="o-1", config={"k": "v"})
        )
    with db.orm_session() as session:
        row = session.scalar(select(TenantRow).where(TenantRow.name == "t-1"))
    assert row is not None
    assert row.owner == "o-1"
    assert row.config == {"k": "v"}
    assert row.gmt_create is not None
