"""SQLite contract tests for the legacy DeviceTtlTimer queries.

Caller container bindings are marked with
``apply_reason=caller_instance:<bot_id>``. Both legacy scan groups must skip
them so an idle caller container keeps its own TTL instead of being renewed
forever:

* ``list_bindings_by_id_asc`` — the ac_entity_device_binding group
* ``list_baas_devices_by_id_asc`` — the baas_device group (the device row's
  ``device_uuid`` equals the caller binding's ``device_id``)
"""

from __future__ import annotations

import pytest

from secbaas.community.core.database import db_manager
from secbaas.community.core.repository.device._orm_model import DeviceModel
from secbaas.community.core.repository.device_binding import (
    OrmDeviceBindingRepository,
)
from secbaas.community.core.repository.device_binding._orm_model import (
    DeviceBindingModel,
)
from secbaas.community.plugins.database.sqlite.sqlite_orm import SqliteOrmPlugin
from tests.utils.external_schema import create_external_tables

ENV = "pre"
DUE = "2020-01-01T00:00:00"


@pytest.fixture
def plugin():
    plugin = SqliteOrmPlugin()
    db_manager.init_plugin(plugin)
    plugin.create_all()
    create_external_tables(plugin._sync_engine)
    return plugin


@pytest.fixture
def repo(plugin):
    return OrmDeviceBindingRepository(database=db_manager)


def _seed_binding(
    *,
    id_val: int,
    device_id: str,
    device_provider: str,
    apply_reason: str | None,
) -> None:
    with db_manager.orm_session() as session:
        session.add(
            DeviceBindingModel(
                id=id_val,
                entity_id=f"entity-{id_val}",
                entity_type="staff",
                device_id=device_id,
                device_provider=device_provider,
                env=ENV,
                status="ACTIVE",
                applied_by="tester",
                apply_reason=apply_reason,
                device_props=(
                    f'{{"sandbox_id": "sb-{device_id}", '
                    f'"ttl_expiration_time": "{DUE}"}}'
                ),
            )
        )


def _seed_device(
    *,
    id_val: int,
    device_uuid: str,
    sandbox_id: str,
) -> None:
    with db_manager.orm_session() as session:
        session.add(
            DeviceModel(
                id=id_val,
                device_uuid=device_uuid,
                tenant="tenant-t",
                env=ENV,
                domain="",
                creator="creator-c",
                modifier="modifier-m",
                status="ACTIVE",
                provider_type="ARCA",
                provider_device_id=sandbox_id,
                provider_device_props=(
                    f'{{"sandbox_id": "{sandbox_id}", "ttl_expiration_time": "{DUE}"}}'
                ),
                is_deleted=0,
            )
        )


def test_list_bindings_excludes_caller_instance_binding(repo):
    _seed_binding(
        id_val=1,
        device_id="caller-device",
        device_provider="baas",
        apply_reason="caller_instance:bot-1",
    )
    _seed_binding(
        id_val=2,
        device_id="normal-device",
        device_provider="arca",
        apply_reason="manual",
    )
    _seed_binding(
        id_val=3,
        device_id="legacy-device",
        device_provider="arca",
        apply_reason=None,
    )

    rows = repo.list_bindings_by_id_asc(limit=100)

    assert [row.id for row in rows] == [2, 3]


def test_list_baas_devices_excludes_caller_instance_device(repo):
    _seed_binding(
        id_val=1,
        device_id="caller-device",
        device_provider="baas",
        apply_reason="caller_instance:bot-1",
    )
    _seed_device(id_val=10, device_uuid="caller-device", sandbox_id="sb-caller")
    _seed_device(id_val=11, device_uuid="normal-device", sandbox_id="sb-normal")

    rows = repo.list_baas_devices_by_id_asc(limit=100)

    assert [row["id"] for row in rows] == [11]
