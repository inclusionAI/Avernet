# mypy: disable-error-code="arg-type,attr-defined"
"""Integration tests for the UPDATE publish PENDING-clone lifecycle.

Covers OpenSpec change ``ensure-one-non-deleted-bot-instance`` —
spec ``pending-clone-lifecycle`` — at the service level:

* Terminal publish outcomes resolve the UPDATE PENDING clone exactly once
  (reject / revoke / stale auto-fail / admin force-success): the still-PENDING
  clone recorded in ``extra_config["target_bot_id"]`` is soft-deleted with the
  self-id tombstone convention (``is_deleted = row.id``) before the terminal
  publish status is written, and the next UPDATE create for the uuid succeeds
  without a slot conflict.
* Per-publish cleanup is strictly scoped to its own ``target_bot_id`` and is a
  defensive no-op for missing pointers and non-PENDING targets.
* Publish creation reconciles unclaimed clones across ALL live generations of
  the bot uuid (creation-time sweep, crash-window healing, claimed-clone
  preservation, cross-generation active check).

Test -> OpenSpec tasks.md mapping (section 6):

* ``TestTerminalOutcomeResolvesClone``: 6.1, 6.2, 6.3, 6.4
* ``TestCleanupScoping``: 6.5, 6.6
* ``TestCreationTimeReconcile``: 6.7, 6.8, 6.9
* ``TestCrossGenerationCoverage``: 6.10

Tasks 6.15, 6.17 and 6.18 live in ``test_publish_clone_faults.py``.
"""

from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.publish_manage import (
    PublishConflictError,
    PublishStatus,
    PublishType,
)
from secbaas.community.bootstrap import get_container
from secbaas.community.core.service.bot_manage._cloning import (
    clone_bot_record,
    try_insert_pending_bot,
)

from ._publish_clone_support import (
    TEST_ENV,
    _assert_live_pending,
    _assert_tombstoned_clone,
    _backdate_publish_gmt_modified,
    _insert_publish_row,
    _live_pending_rows_for_uuid,
    _ps,
    _publish_repo,
    _rel_tombstones,
    _seed_active_update_source,
    _target_bot_id,
)


def _admin():
    return get_container().services.publish_admin_service()


@pytest.mark.integration
class TestTerminalOutcomeResolvesClone:
    """Every terminal UPDATE publish outcome frees the PENDING clone slot."""

    @pytest.mark.asyncio
    async def test_rejected_update_abandons_clone_and_unblocks_next_create(
        self,
        db_manager,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.1 — rejected: clone + rels self-id tombstoned; next UPDATE create ok."""
        tenant_name, source_id, device_uuid = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)
        clone_id = _target_bot_id(publish)
        created_bot_ids.append(clone_id)

        from .conftest import create_test_bot_device_rel

        planted_rel_id = create_test_bot_device_rel(
            rel_repository,
            bot_id=clone_id,
            device_uuid=device_uuid,
            tenant=tenant_name,
            created_rel_ids=created_rel_ids,
        )

        rejected = await _ps().reject_publish(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_approver",
            reason="clone lifecycle test",
        )
        assert rejected.status == PublishStatus.REJECTED.value
        _assert_tombstoned_clone(bot_repository, tenant_name, clone_id)
        assert (
            _rel_tombstones(db_manager, tenant_name, clone_id).get(planted_rel_id)
            == planted_rel_id
        ), "clone rel rows must be tombstoned with the clone"

        second = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(second.id)
        second_clone = _target_bot_id(second)
        created_bot_ids.append(second_clone)
        assert second.status == PublishStatus.PENDING.value
        assert second.id != publish.id
        assert second_clone != clone_id
        _assert_live_pending(bot_repository, tenant_name, second_clone)

    @pytest.mark.asyncio
    async def test_revoked_update_abandons_clone_and_unblocks_next_create(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.2 — revoked (from APPROVING): same clone disposal; next UPDATE ok."""
        tenant_name, source_id, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)
        clone_id = _target_bot_id(publish)
        created_bot_ids.append(clone_id)

        # revoke_publish is only valid from APPROVING; seed that via the repo
        # so the publish stays non-terminal until revoke's cleanup runs.
        _publish_repo().update_status(
            publish_id=publish.id,
            tenant=tenant_name,
            env=TEST_ENV,
            status="APPROVING",
            modifier="test_user",
        )

        revoked = await _ps().revoke_publish(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_operator",
            reason="clone lifecycle test",
        )
        assert revoked.status == PublishStatus.REVOKED.value
        _assert_tombstoned_clone(bot_repository, tenant_name, clone_id)

        second = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(second.id)
        created_bot_ids.append(_target_bot_id(second))
        assert second.status == PublishStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_stale_update_publish_auto_fail_frees_slot_before_recreate(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.3 — stale resolution cleans the stale clone before the new create."""
        tenant_name, source_id, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)
        clone_id = _target_bot_id(publish)
        created_bot_ids.append(clone_id)
        _backdate_publish_gmt_modified(publish.id)

        fresh = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(fresh.id)
        fresh_clone = _target_bot_id(fresh)
        created_bot_ids.append(fresh_clone)

        stale = _publish_repo().get_by_id(publish.id, tenant_name, TEST_ENV)
        assert stale.status == PublishStatus.FAILED.value
        _assert_tombstoned_clone(bot_repository, tenant_name, clone_id)
        assert fresh.status == PublishStatus.PENDING.value
        assert fresh.id != publish.id
        assert fresh_clone != clone_id
        _assert_live_pending(bot_repository, tenant_name, fresh_clone)

    @pytest.mark.asyncio
    async def test_force_success_retires_clone_and_keeps_incumbent_active(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.4 — force-success abandons the clone; incumbent stays ACTIVE."""
        tenant_name, source_id, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)
        clone_id = _target_bot_id(publish)
        created_bot_ids.append(clone_id)
        source_row = bot_repository.get_by_id(source_id, tenant_name, TEST_ENV)
        assert source_row is not None

        await _admin().force_success(
            publish_id=publish.id,
            tenant=tenant_name,
            modifier="admin_user",
        )

        forced = _publish_repo().get_by_id(publish.id, tenant_name, TEST_ENV)
        assert forced.status == PublishStatus.SUCCESS.value
        _assert_tombstoned_clone(bot_repository, tenant_name, clone_id)
        live_rows = bot_repository.list_by_bot_uuid(
            source_row.bot_uuid, tenant_name, TEST_ENV
        )
        assert [r.id for r in live_rows] == [source_id]
        assert live_rows[0].status == BotStatus.ACTIVE.value

        second = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(second.id)
        created_bot_ids.append(_target_bot_id(second))
        assert second.status == PublishStatus.PENDING.value


@pytest.mark.integration
class TestCleanupScoping:
    """Terminal cleanup is scoped to the publish's own target_bot_id."""

    @pytest.mark.asyncio
    async def test_reject_cleanup_spares_concurrent_publish_clone(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.5 — cleanup for publish A never touches publish B's live PENDING clone.

        The bot unique key keeps one uuid from holding two live PENDING
        clones, so concurrent publish B is a distinct bot_uuid with its own
        live clone — the shape the scoping must not disturb.
        """
        tenant_a, source_a, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        tenant_b, source_b, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        publish_a = await _ps().create_publish(
            tenant=tenant_a,
            bot_id=source_a,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        publish_b = await _ps().create_publish(
            tenant=tenant_b,
            bot_id=source_b,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.extend([publish_a.id, publish_b.id])
        clone_a = _target_bot_id(publish_a)
        clone_b = _target_bot_id(publish_b)
        created_bot_ids.extend([clone_a, clone_b])

        rejected = await _ps().reject_publish(
            tenant=tenant_a,
            publish_id=publish_a.id,
            operator="test_approver",
            reason="scoping test",
        )
        assert rejected.status == PublishStatus.REJECTED.value

        _assert_tombstoned_clone(bot_repository, tenant_a, clone_a)
        _assert_live_pending(bot_repository, tenant_b, clone_b)
        still_live = _publish_repo().get_by_id(publish_b.id, tenant_b, TEST_ENV)
        assert still_live.status == PublishStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_reject_noops_when_target_bot_id_missing(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.6a — crash-window shape: publish without a target_bot_id pointer."""
        from .conftest import create_test_bot_record, create_test_tenant

        tenant_name, _ = create_test_tenant(created_tenant_ids, created_template_ids)
        source_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )
        publish_id = _insert_publish_row(tenant_name, source_id)

        rejected = await _ps().reject_publish(
            tenant=tenant_name,
            publish_id=publish_id,
            operator="test_approver",
            reason="missing-pointer tolerance",
        )
        created_publish_ids.append(publish_id)
        assert rejected.status == PublishStatus.REJECTED.value

        source_row = bot_repository.get_by_id(source_id, tenant_name, TEST_ENV)
        assert source_row is not None
        live_rows = bot_repository.list_by_bot_uuid(
            source_row.bot_uuid, tenant_name, TEST_ENV
        )
        assert [r.id for r in live_rows] == [source_id]
        assert live_rows[0].status == BotStatus.ACTIVE.value
        assert live_rows[0].is_deleted == 0

    @pytest.mark.asyncio
    async def test_reject_noops_when_target_row_not_pending(
        self,
        bot_repository,
        created_bot_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.6b — target row is no longer a live PENDING row (already ACTIVE)."""
        from .conftest import create_test_bot_record, create_test_tenant

        tenant_name, _ = create_test_tenant(created_tenant_ids, created_template_ids)
        source_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )
        publish_id = _insert_publish_row(
            tenant_name, source_id, extra_config={"target_bot_id": source_id}
        )

        rejected = await _ps().reject_publish(
            tenant=tenant_name,
            publish_id=publish_id,
            operator="test_approver",
            reason="resolved-target tolerance",
        )
        created_publish_ids.append(publish_id)
        assert rejected.status == PublishStatus.REJECTED.value

        source_row = bot_repository.get_by_id(source_id, tenant_name, TEST_ENV)
        assert source_row is not None
        assert source_row.status == BotStatus.ACTIVE.value
        assert source_row.is_deleted == 0


@pytest.mark.integration
class TestCreationTimeReconcile:
    """The reconcile sweep heals garbage clones when no publish claims the slot."""

    @pytest.mark.asyncio
    async def test_terminal_leak_clone_reclaimed_by_next_create(
        self,
        db_manager,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.7 — terminal-leak shape (force-success): live PENDING clone with no
        non-terminal publish is swept at the next UPDATE create."""
        tenant_name, source_id, device_uuid = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        leaked_clone = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=tenant_name,
            env=TEST_ENV,
        )
        created_bot_ids.append(leaked_clone)
        leaked_publish_id = _insert_publish_row(
            tenant_name,
            source_id,
            status=PublishStatus.SUCCESS.value,
            extra_config={"target_bot_id": leaked_clone},
        )
        created_publish_ids.append(leaked_publish_id)

        fresh = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(fresh.id)
        fresh_clone = _target_bot_id(fresh)
        created_bot_ids.append(fresh_clone)

        _assert_tombstoned_clone(bot_repository, tenant_name, leaked_clone)
        assert fresh.status == PublishStatus.PENDING.value
        assert fresh_clone != leaked_clone
        _assert_live_pending(bot_repository, tenant_name, fresh_clone)

    @pytest.mark.asyncio
    async def test_crash_window_orphan_heals_on_next_create(
        self,
        db_manager,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.8 — crash-window shape: live clone + batch-less pointer-less PENDING
        publish; the next create auto-fails the orphan and sweeps the clone."""
        tenant_name, source_id, device_uuid = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        orphan_clone = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=tenant_name,
            env=TEST_ENV,
        )
        created_bot_ids.append(orphan_clone)
        orphan_publish_id = _insert_publish_row(tenant_name, source_id)
        created_publish_ids.append(orphan_publish_id)

        fresh = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(fresh.id)
        fresh_clone = _target_bot_id(fresh)
        created_bot_ids.append(fresh_clone)

        orphan_after = _publish_repo().get_by_id(
            orphan_publish_id, tenant_name, TEST_ENV
        )
        assert orphan_after.status == PublishStatus.FAILED.value
        _assert_tombstoned_clone(bot_repository, tenant_name, orphan_clone)
        assert fresh.status == PublishStatus.PENDING.value
        assert fresh_clone != orphan_clone
        _assert_live_pending(bot_repository, tenant_name, fresh_clone)

    @pytest.mark.asyncio
    async def test_claimed_clone_preserved_by_idempotent_and_conflict_branches(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.9 — a live publish holding its PENDING clone claims it; no deletes."""
        tenant_name, source_id, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)
        clone_id = _target_bot_id(publish)
        created_bot_ids.append(clone_id)

        same_type = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        assert same_type.id == publish.id
        assert same_type.status == publish.status

        with pytest.raises(PublishConflictError):
            await _ps().create_publish(
                tenant=tenant_name,
                bot_id=source_id,
                publish_type=PublishType.RESTART,
                operator="test_user",
                request_id=uuid4().hex,
            )

        _assert_live_pending(bot_repository, tenant_name, clone_id)
        source_row = bot_repository.get_by_id(source_id, tenant_name, TEST_ENV)
        assert source_row is not None
        assert source_row.status == BotStatus.ACTIVE.value
        assert source_row.is_deleted == 0

    @pytest.mark.asyncio
    async def test_pending_generation_owning_devices_is_never_reconciled(
        self,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """D5.8 — a live PENDING generation whose rel is its device's ONLY live
        binding holds real ownership (baas_bot_device_rel is the authoritative
        record, #2648) and is never swept, even with no publish claiming it."""
        from .conftest import create_test_bot_device_rel

        tenant_name, source_id, device_uuid = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            created_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        source_rel = rel_repository.get_by_device_uuid(
            device_uuid, tenant_name, TEST_ENV
        )
        assert source_rel is not None
        rel_repository.soft_delete(
            rel_id=source_rel.id, tenant=tenant_name, env=TEST_ENV, modifier="test_user"
        )
        owner_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=tenant_name,
            env=TEST_ENV,
        )
        created_bot_ids.append(owner_id)
        create_test_bot_device_rel(
            rel_repository,
            bot_id=owner_id,
            device_uuid=device_uuid,
            tenant=tenant_name,
            created_rel_ids=created_rel_ids,
        )

        with pytest.raises(PublishConflictError):
            await _ps().create_publish(
                tenant=tenant_name,
                bot_id=source_id,
                publish_type=PublishType.UPDATE,
                operator="test_user",
                request_id=uuid4().hex,
            )

        _assert_live_pending(bot_repository, tenant_name, owner_id)
        owner_rel = rel_repository.get_by_device_uuid(
            device_uuid, tenant_name, TEST_ENV
        )
        assert owner_rel is not None and owner_rel.bot_id == owner_id
        assert owner_rel.is_deleted == 0
