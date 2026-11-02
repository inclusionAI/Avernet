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
class TestCrossGenerationCoverage:
    """The active-publish check scans every live generation of the uuid."""

    @pytest.mark.asyncio
    async def test_idempotent_return_via_other_generation_row(
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
        """6.10a — create targeting the CLONE row finds the publish attached to the
        SOURCE row (older live generation) and returns it idempotently."""
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

        via_clone = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=clone_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        assert via_clone.id == publish.id, (
            "the generation-scoped check must find the publish attached to "
            "the source generation row instead of colliding with the clone slot"
        )
        _assert_live_pending(bot_repository, tenant_name, clone_id)
        source_row = bot_repository.get_by_id(source_id, tenant_name, TEST_ENV)
        assert source_row is not None
        assert source_row.status == BotStatus.ACTIVE.value
        assert source_row.is_deleted == 0

    @pytest.mark.asyncio
    async def test_stale_resolution_via_clone_generation_row(
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
        """6.10b — stale PENDING UPDATE publish attached to the CLONE generation
        row: found and resolved per its age, then the clone is swept."""
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
        clone_id = try_insert_pending_bot(
            bot_repository,
            source_bot_id=source_id,
            tenant=tenant_name,
            env=TEST_ENV,
        )
        created_bot_ids.append(clone_id)
        # Attached to the clone row, so a single-generation lookup on the
        # source id cannot see this publish — the uuid-scoped check must.
        stale_publish_id = _insert_publish_row(tenant_name, clone_id, with_batch=True)
        created_publish_ids.append(stale_publish_id)
        _backdate_publish_gmt_modified(stale_publish_id)

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

        stale_after = _publish_repo().get_by_id(stale_publish_id, tenant_name, TEST_ENV)
        assert stale_after.status == PublishStatus.FAILED.value
        _assert_tombstoned_clone(bot_repository, tenant_name, clone_id)
        assert fresh.status == PublishStatus.PENDING.value
        assert fresh_clone != clone_id
        _assert_live_pending(bot_repository, tenant_name, fresh_clone)

    @pytest.mark.asyncio
    async def test_phantom_publish_on_retired_source_never_blocks_create(
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
        """6.10c — a non-terminal publish on a RETIRED (soft-deleted) source id is
        a phantom guarding no live slot; the create against the current
        generation completes without a spurious 409."""
        from .conftest import (
            create_test_devices_for_bot,
            create_test_tenant,
            generate_unique_bot_uuid,
        )

        tenant_name, _ = create_test_tenant(created_tenant_ids, created_template_ids)
        bot_uuid = generate_unique_bot_uuid()
        retired_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant_name,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.RELEASED.value,
            name=f"Retired Gen {bot_uuid[:8]}",
        )
        created_bot_ids.append(retired_id)
        bot_repository.soft_delete(
            bot_id=retired_id, tenant=tenant_name, env=TEST_ENV, modifier="test_user"
        )
        current_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant_name,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name=f"Current Gen {bot_uuid[:8]}",
        )
        created_bot_ids.append(current_id)
        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=current_id,
            device_status="ACTIVE",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )
        phantom_publish_id = _insert_publish_row(
            tenant_name, retired_id, with_batch=True
        )
        created_publish_ids.append(phantom_publish_id)

        fresh = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=current_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(fresh.id)
        fresh_clone = _target_bot_id(fresh)

        assert fresh.status == PublishStatus.PENDING.value
        _assert_live_pending(bot_repository, tenant_name, fresh_clone)
        live_pending = _live_pending_rows_for_uuid(
            bot_repository, tenant_name, bot_uuid
        )
        assert [r.id for r in live_pending] == [fresh_clone]
        phantom = _publish_repo().get_by_id(phantom_publish_id, tenant_name, TEST_ENV)
        assert phantom.status == PublishStatus.PENDING.value

    async def test_unfinished_publish_on_retired_source_keeps_its_claimed_clone(
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
        """6.10d — a non-terminal publish on a RETIRED source that CLAIMS a live
        PENDING clone keeps that clone: the uuid has no finished publish, so it
        is in-flight and the reconciliation sweep must not delete the claimed
        row. The creation then aborts on the occupied PENDING slot."""
        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
            generate_unique_bot_uuid,
        )

        tenant_name, _ = create_test_tenant(created_tenant_ids, created_template_ids)
        bot_uuid = generate_unique_bot_uuid()
        retired_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant_name,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.RELEASED.value,
            name=f"Retired Gen {bot_uuid[:8]}",
        )
        created_bot_ids.append(retired_id)
        bot_repository.soft_delete(
            bot_id=retired_id, tenant=tenant_name, env=TEST_ENV, modifier="test_user"
        )
        current_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant_name,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.ACTIVE.value,
            name=f"Current Gen {bot_uuid[:8]}",
        )
        created_bot_ids.append(current_id)
        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=current_id,
            device_status="ACTIVE",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )
        claimed_clone_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant_name,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status=BotStatus.PENDING.value,
            name=f"In-flight Clone {bot_uuid[:8]}",
        )
        created_bot_ids.append(claimed_clone_id)
        inflight_publish_id = _insert_publish_row(
            tenant_name,
            retired_id,
            with_batch=True,
            extra_config={"target_bot_id": claimed_clone_id},
        )
        created_publish_ids.append(inflight_publish_id)

        with pytest.raises(PublishConflictError):
            await _ps().create_publish(
                tenant=tenant_name,
                bot_id=current_id,
                publish_type=PublishType.UPDATE,
                operator="test_user",
                request_id=uuid4().hex,
            )

        _assert_live_pending(bot_repository, tenant_name, claimed_clone_id)
        inflight = _publish_repo().get_by_id(inflight_publish_id, tenant_name, TEST_ENV)
        assert inflight.status == PublishStatus.PENDING.value
