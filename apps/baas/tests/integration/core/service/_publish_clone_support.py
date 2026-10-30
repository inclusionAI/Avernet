# mypy: disable-error-code="arg-type,attr-defined"
"""Shared seeding helpers for the pending-clone lifecycle integration tests.

Backs ``test_publish_clone_lifecycle.py`` (OpenSpec tasks 6.1-6.10) and
``test_publish_clone_faults.py`` (tasks 6.17, 6.18) of the
``ensure-one-non-deleted-bot-instance`` change. Only test modules import
this file; there is intentionally no conftest edit.
"""

import re
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.publish_manage import (
    DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS,
)
from secbaas.community.bootstrap import get_container
from secbaas.community.core.utils.env_utils import get_current_env

TEST_ENV = get_current_env()

# Each relaxed_bot_pending_slot activation gets its own 1M+ bot-id range so
# live publishes seeded under a relaxed table can never collide with the
# session-keyed publish active-slot rows of later tests.
_relaxed_id_floor: list[int] = [0]


def _ps():
    return get_container().services.publish_service()


def _publish_repo():
    return get_container().repository.publish_repository()


def _batch_repo():
    return get_container().repository.publish_batch_repository()


def _seed_active_update_source(
    bot_repository,
    device_repository,
    rel_repository,
    created_bot_ids,
    created_device_ids,
    created_rel_ids,
    created_tenant_ids,
    created_template_ids,
):
    """Seed tenant + live ACTIVE source bot + one UPDATE-eligible device/rel.

    ``created_bot_ids`` may be a throwaway list when the caller runs under
    ``relaxed_bot_pending_slot`` — those bot rows live and die inside the
    relaxed table and must not be deleted from the restored keyed table by
    the session cleanup.
    """
    from .conftest import (
        create_test_bot_record,
        create_test_devices_for_bot,
        create_test_tenant,
    )

    tenant_name, _template_id = create_test_tenant(
        created_tenant_ids, created_template_ids
    )
    bot_id = create_test_bot_record(
        bot_repository,
        tenant=tenant_name,
        status=BotStatus.ACTIVE.value,
        created_bot_ids=created_bot_ids,
    )
    devices = create_test_devices_for_bot(
        device_repository=device_repository,
        rel_repository=rel_repository,
        tenant=tenant_name,
        bot_id=bot_id,
        device_status="ACTIVE",
        num_devices=1,
        created_device_ids=created_device_ids,
        created_rel_ids=created_rel_ids,
    )
    return tenant_name, bot_id, devices[0]["device_uuid"]


def _target_bot_id(publish):
    assert publish.extra_config is not None
    target = publish.extra_config.target_bot_id
    assert target is not None
    return target


def _insert_publish_row(
    tenant,
    bot_id,
    *,
    status="PENDING",
    publish_type="UPDATE",
    extra_config=None,
    with_batch=False,
    backdate_seconds=None,
):
    """Seed a baas_publish row directly (plus one batch / backdating on demand).

    Publishes seeded here are the corruption shapes of the spec: crash-window
    orphans (no batch, no target pointer), terminal leaks holding a pointer,
    cross-generation phantoms, and pre-UK duplicates claiming a clone.
    """
    from secbaas.community.api.publish_manage import PublishBatchConfig

    publish_id = _publish_repo().insert_publish(
        tenant=tenant,
        env=TEST_ENV,
        domain="test_domain",
        bot_id=bot_id,
        publish_type=publish_type,
        status=status,
        creator="test_user",
        modifier="test_user",
        extra_config=extra_config,
        name="Publish-" + publish_type,
        publisher="test_user",
        replica_desired=1,
        batch_capacity=5,
        batch_number=1,
        cooldown_seconds=0,
    )
    if with_batch:
        _batch_repo().insert_batch(
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            publish_id=publish_id,
            bot_id=bot_id,
            batch_index=0,
            batch_capacity=1,
            status="PENDING",
            creator="test_user",
            modifier="test_user",
            extra_config=PublishBatchConfig(
                stage="PROD_FIRST_BATCH",
                cooldown_seconds=0,
                device_count=1,
            ).model_dump(),
        )
    if backdate_seconds is not None:
        _backdate_publish_gmt_modified(publish_id, seconds=backdate_seconds)
    return publish_id


def _backdate_publish_gmt_modified(publish_id, seconds=None):
    """Back-date gmt_modified via the repo session clock (stale-seed helper)."""
    from sqlalchemy import text

    from secbaas.community.core.database import db_manager

    if seconds is None:
        seconds = DEFAULT_PUBLISH_LEVEL_TIMEOUT_SECONDS * 2
    cutoff = _publish_repo().now() - timedelta(seconds=seconds)
    with db_manager.orm_session() as session:
        session.execute(
            text("UPDATE baas_publish SET gmt_modified = :cutoff WHERE id = :pid"),
            {"cutoff": cutoff, "pid": publish_id},
        )


def _assert_tombstoned_clone(bot_repository, tenant, clone_id):
    """Clones are soft-deleted with the self-id tombstone and never revived."""
    row = bot_repository.get_by_id_including_deleted(clone_id, tenant, TEST_ENV)
    assert row is not None, f"clone {clone_id} row vanished — rows are never deleted"
    assert row.is_deleted == clone_id, (
        f"expected self-id tombstone is_deleted=={clone_id}, got {row.is_deleted}"
    )
    assert row.status == BotStatus.PENDING.value, (
        f"clone {clone_id} must stay PENDING when abandoned, got {row.status}"
    )


def _assert_live_pending(bot_repository, tenant, clone_id):
    row = bot_repository.get_by_id(clone_id, tenant, TEST_ENV)
    assert row is not None, f"clone {clone_id} was expected to remain live"
    assert row.is_deleted == 0
    assert row.status == BotStatus.PENDING.value


def _rel_tombstones(db_manager, tenant, bot_id):
    """Map rel_id -> is_deleted for every (incl. soft-deleted) rel row of a bot."""
    from sqlalchemy import text

    with db_manager.orm_session() as session:
        rows = session.execute(
            text(
                "SELECT id, is_deleted FROM baas_bot_device_rel "
                "WHERE tenant = :t AND env = :e AND bot_id = :b"
            ),
            {"t": tenant, "e": TEST_ENV, "b": bot_id},
        ).fetchall()
    return {row[0]: row[1] for row in rows}


def _live_pending_rows_for_uuid(bot_repository, tenant, bot_uuid):
    return [
        r
        for r in bot_repository.list_by_bot_uuid(bot_uuid, tenant, TEST_ENV)
        if r.status == BotStatus.PENDING.value
    ]


def _install_cleanup_failure(monkeypatch, bot_repository, target_bot_id):
    """Fail the bot-repo soft_delete of target_bot_id during clone cleanup.

    The cleanup helper tolerates a failing rel soft-delete but the bot-row
    soft_delete must propagate (design D2). Patching the shared bot-repository
    singleton keeps every other method real; monkeypatch restores it at
    teardown.
    """
    real_soft_delete = type(bot_repository).soft_delete

    def _failing_soft_delete(*, bot_id, **kwargs):
        if bot_id == target_bot_id:
            raise RuntimeError("injected clone-cleanup failure")
        return real_soft_delete(bot_repository, bot_id=bot_id, **kwargs)

    monkeypatch.setattr(bot_repository, "soft_delete", _failing_soft_delete)


@contextmanager
def _relaxed_bot_slot(db_manager):
    """Temporarily drop uk_tnt_bot_uuid_env_status_del from the live SQLite.

    Task 6.17 pins the healing of pre-unique-key corruption — two live PENDING
    rows per uuid — which the now-enforced key makes unplantable through any
    normal insert. This fixture recreates ``baas_bot`` from its sqlite_master
    DDL with the UK stripped, stashes the keyed table under a random name, and
    seeds the relaxed table with the stash rows plus a high-id floor marker so
    new bot ids continue in a fixture-unique range (the publish active-slot UK
    makes recycled low ids collide with earlier tests' live publishes).
    Teardown drops the relaxed table and renames the keyed table back even
    when the test fails: bot rows created inside the relaxed table disappear
    with it and must not be tracked for session cleanup.
    """
    from sqlalchemy import text

    engine = getattr(db_manager._plugin, "_sync_engine", None)
    if engine is None or engine.dialect.name != "sqlite":
        pytest.skip("pre-corruption duplicate planting requires SQLite surgery")

    stash_table = f"baas_bot_stash_uk_{uuid4().hex[:8]}"
    with db_manager.orm_session() as session:
        ddl = session.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type = 'table' AND name = 'baas_bot'"
            )
        ).scalar()
        assert ddl is not None, "baas_bot table is missing from sqlite_master"
        relaxed = re.sub(
            r",\s*CONSTRAINT\s+\"?uk_tnt_bot_uuid_env_status_del\"?\s+UNIQUE\s*\([^)]*\)",
            "",
            ddl,
        )
        assert relaxed != ddl, "uk_tnt_bot_uuid_env_status_del missing from DDL"
        session.execute(text(f"ALTER TABLE baas_bot RENAME TO {stash_table}"))
        session.execute(text(relaxed))
        session.execute(text(f"INSERT INTO baas_bot SELECT * FROM {stash_table}"))
        floor = _relaxed_id_floor[0] + 1_000_000
        session.execute(
            text(
                "INSERT INTO baas_bot (id, gmt_create, gmt_modified, tenant, env, "
                "domain, is_deleted, creator, modifier, bot_uuid, status, name) "
                "VALUES (:id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, :tenant, :env, "
                "'test_domain', 0, 'system', 'system', :uuid, 'ACTIVE', "
                "'relaxed-id-floor-marker')"
            ),
            {
                "id": floor,
                "tenant": f"relaxed_floor_{floor}",
                "env": TEST_ENV,
                "uuid": f"relaxed-floor-{floor}",
            },
        )
    _relaxed_id_floor[0] = floor
    try:
        yield stash_table
    finally:
        with db_manager.orm_session() as session:
            session.execute(text("DROP TABLE IF EXISTS baas_bot"))
            session.execute(text(f"ALTER TABLE {stash_table} RENAME TO baas_bot"))


def _plant_pending_duplicate(db_manager, source_bot_id, new_id):
    """Plant a pre-corruption duplicate: copy the source row as a live PENDING clone.

    Must run under ``relaxed_bot_pending_slot`` (the relaxed baas_bot table has
    no UK). Column names come from PRAGMA table_info so the copy keeps working
    when the table gains columns.
    """
    from sqlalchemy import text

    with db_manager.orm_session() as session:
        columns = [
            row[1] for row in session.execute(text("PRAGMA table_info(baas_bot)"))
        ]
        select_exprs = []
        for column in columns:
            if column == "id":
                select_exprs.append(":new_id")
            elif column == "status":
                select_exprs.append("'PENDING'")
            else:
                select_exprs.append(f"s.{column}")
        session.execute(
            text(
                f"INSERT INTO baas_bot ({', '.join(columns)}) "
                f"SELECT {', '.join(select_exprs)} FROM baas_bot s "
                f"WHERE s.id = :source_id"
            ),
            {"new_id": new_id, "source_id": source_bot_id},
        )
        planted = session.execute(
            text(
                "SELECT COUNT(*) FROM baas_bot "
                "WHERE id = :new_id AND is_deleted = 0 AND status = 'PENDING'"
            ),
            {"new_id": new_id},
        ).scalar()
        assert planted == 1, "duplicate planting failed"
    return new_id
