# mypy: disable-error-code="arg-type,attr-defined"
"""Integration tests for corruption healing and cleanup-fault ordering.

Continues the coverage of OpenSpec change
``ensure-one-non-deleted-bot-instance`` (spec ``pending-clone-lifecycle``):

* Task 6.17 — pre-unique-key duplicate clones: two live PENDING rows for one
  uuid are reconciled together by the next creation when unclaimed, and are
  fully preserved when a live publish claims the pair (idempotent-return and
  different-type-conflict branches delete nothing). This also proves the D4
  migration pre-flight story: traffic heals duplicates before the bot UK ALTER.
* Task 6.18 — design D2 ordering: when terminal-path clone cleanup raises an
  injected repository error in reject / revoke / stale auto-fail /
  force-success, the publish stays non-terminal and the error surfaces to the
  caller (no swallow).

Task 6.15 (regression pin) is satisfied by running the existing service and
repository suites unchanged alongside these tests.
"""

from uuid import uuid4

import pytest

from secbaas.community.api.publish_manage import (
    PublishConflictError,
    PublishStatus,
    PublishType,
)
from secbaas.community.bootstrap import get_container

from ._publish_clone_support import (
    TEST_ENV,
    _assert_live_pending,
    _assert_tombstoned_clone,
    _backdate_publish_gmt_modified,
    _insert_publish_row,
    _install_cleanup_failure,
    _live_pending_rows_for_uuid,
    _plant_pending_duplicate,
    _ps,
    _publish_repo,
    _relaxed_bot_slot,
    _seed_active_update_source,
    _target_bot_id,
)


def _admin():
    return get_container().services.publish_admin_service()


@pytest.fixture
def relaxed_bot_pending_slot(db_manager):
    with _relaxed_bot_slot(db_manager) as stash_table:
        yield stash_table


@pytest.mark.integration
class TestPreCorruptedDuplicates:
    """Pre-UK duplicate live PENDING rows: reconcile-all vs claim-preservation."""

    @pytest.mark.asyncio
    async def test_unclaimed_duplicate_pair_reconciled_in_one_pass(
        self,
        db_manager,
        relaxed_bot_pending_slot,
        bot_repository,
        device_repository,
        rel_repository,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.17a — duplicate pair + zero non-terminal publishes: the next UPDATE
        creation sweeps BOTH clones in one pass (self-id tombstones, no
        revival) and completes with exactly one fresh live PENDING clone."""
        # Bot rows created under the relaxed schema live and die inside the
        # relaxed table — tracking them for session cleanup would delete
        # unrelated rows from the restored keyed table, so use a throwaway.
        throwaway_bot_ids: list[int] = []
        tenant_name, source_id, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            throwaway_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        dup_one_id = _plant_pending_duplicate(db_manager, source_id, 10_000_001)
        dup_two_id = _plant_pending_duplicate(db_manager, source_id, 10_000_002)
        bot_uuid = bot_repository.get_by_id(source_id, tenant_name, TEST_ENV).bot_uuid

        fresh = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(fresh.id)
        fresh_clone = _target_bot_id(fresh)

        for dup_id in (dup_one_id, dup_two_id):
            _assert_tombstoned_clone(bot_repository, tenant_name, dup_id)
        live_pending = _live_pending_rows_for_uuid(
            bot_repository, tenant_name, bot_uuid
        )
        assert [r.id for r in live_pending] == [fresh_clone], (
            "exactly one fresh live PENDING clone must remain after reconcile"
        )
        assert fresh.status == PublishStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_claimed_duplicate_pair_deletes_nothing(
        self,
        db_manager,
        relaxed_bot_pending_slot,
        bot_repository,
        device_repository,
        rel_repository,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
        created_tenant_ids,
        created_template_ids,
    ):
        """6.17b — the same pair WITH a live claiming publish: idempotent-return
        and different-type-conflict branches delete no bot rows."""
        throwaway_bot_ids: list[int] = []
        tenant_name, source_id, _ = _seed_active_update_source(
            bot_repository,
            device_repository,
            rel_repository,
            throwaway_bot_ids,
            created_device_ids,
            created_rel_ids,
            created_tenant_ids,
            created_template_ids,
        )
        dup_one_id = _plant_pending_duplicate(db_manager, source_id, 10_000_001)
        dup_two_id = _plant_pending_duplicate(db_manager, source_id, 10_000_002)
        claiming_publish_id = _insert_publish_row(
            tenant_name,
            source_id,
            with_batch=True,
            extra_config={"target_bot_id": dup_one_id},
        )
        created_publish_ids.append(claiming_publish_id)

        same_type = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=source_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        assert same_type.id == claiming_publish_id

        with pytest.raises(PublishConflictError):
            await _ps().create_publish(
                tenant=tenant_name,
                bot_id=source_id,
                publish_type=PublishType.RESTART,
                operator="test_user",
                request_id=uuid4().hex,
            )

        for dup_id in (dup_one_id, dup_two_id):
            _assert_live_pending(bot_repository, tenant_name, dup_id)
        claim_row = _publish_repo().get_by_id(
            claiming_publish_id, tenant_name, TEST_ENV
        )
        assert claim_row.status == PublishStatus.PENDING.value
        source_row = bot_repository.get_by_id(source_id, tenant_name, TEST_ENV)
        assert source_row is not None
        assert source_row.is_deleted == 0


@pytest.mark.integration
class TestCleanupFailureKeepsPublishNonTerminal:
    """Injected cleanup errors surface and never hide behind a terminal status."""

    @pytest.mark.asyncio
    async def test_reject_with_failing_cleanup_keeps_publish_pending(
        self,
        monkeypatch,
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

        _install_cleanup_failure(monkeypatch, bot_repository, clone_id)
        with pytest.raises(RuntimeError, match="injected clone-cleanup failure"):
            await _ps().reject_publish(
                tenant=tenant_name,
                publish_id=publish.id,
                operator="test_approver",
                reason="fault injection",
            )

        still = _publish_repo().get_by_id(publish.id, tenant_name, TEST_ENV)
        assert still.status == PublishStatus.PENDING.value, (
            "a cleanup failure must leave the publish non-terminal (D2)"
        )
        _assert_live_pending(bot_repository, tenant_name, clone_id)

    @pytest.mark.asyncio
    async def test_revoke_with_failing_cleanup_keeps_publish_approving(
        self,
        monkeypatch,
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
        _publish_repo().update_status(
            publish_id=publish.id,
            tenant=tenant_name,
            env=TEST_ENV,
            status="APPROVING",
            modifier="test_user",
        )

        _install_cleanup_failure(monkeypatch, bot_repository, clone_id)
        with pytest.raises(RuntimeError, match="injected clone-cleanup failure"):
            await _ps().revoke_publish(
                tenant=tenant_name,
                publish_id=publish.id,
                operator="test_operator",
                reason="fault injection",
            )

        still = _publish_repo().get_by_id(publish.id, tenant_name, TEST_ENV)
        assert still.status == "APPROVING", (
            "a cleanup failure must leave the publish non-terminal (D2)"
        )
        _assert_live_pending(bot_repository, tenant_name, clone_id)

    @pytest.mark.asyncio
    async def test_stale_resolution_with_failing_cleanup_keeps_publish_pending(
        self,
        monkeypatch,
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

        _install_cleanup_failure(monkeypatch, bot_repository, clone_id)
        with pytest.raises(RuntimeError, match="injected clone-cleanup failure"):
            await _ps().create_publish(
                tenant=tenant_name,
                bot_id=source_id,
                publish_type=PublishType.UPDATE,
                operator="test_user",
                request_id=uuid4().hex,
            )

        still = _publish_repo().get_by_id(publish.id, tenant_name, TEST_ENV)
        assert still.status == PublishStatus.PENDING.value, (
            "the stale publish must stay non-terminal when its clone cleanup fails"
        )
        _assert_live_pending(bot_repository, tenant_name, clone_id)

    @pytest.mark.asyncio
    async def test_force_success_with_failing_retire_keeps_publish_non_terminal(
        self,
        monkeypatch,
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

        _install_cleanup_failure(monkeypatch, bot_repository, clone_id)
        with pytest.raises(RuntimeError, match="injected clone-cleanup failure"):
            await _admin().force_success(
                publish_id=publish.id,
                tenant=tenant_name,
                modifier="admin_user",
            )

        still = _publish_repo().get_by_id(publish.id, tenant_name, TEST_ENV)
        assert still.status == PublishStatus.PENDING.value, (
            "a retire failure must leave the forced publish non-terminal (D2/D3)"
        )
        _assert_live_pending(bot_repository, tenant_name, clone_id)
