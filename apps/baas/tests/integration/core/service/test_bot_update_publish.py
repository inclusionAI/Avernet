# mypy: disable-error-code="arg-type,attr-defined"
"""Integration tests for UPDATE publish flow.

Tests the full lifecycle: create_publish → bot record creation →
complete_publish with relationship transfer.
"""

from uuid import uuid4

import pytest

from secbaas.community.api.bot_manage import BotStatus
from secbaas.community.api.publish_manage import (
    PublishConfig,
    PublishStatus,
    PublishType,
)
from secbaas.community.bootstrap import get_container
from secbaas.community.core.utils.env_utils import get_current_env


def _ps():
    return get_container().services.publish_service()


@pytest.mark.integration
class TestUpdatePublishCreate:
    """Integration tests for UPDATE publish creation."""

    @pytest.mark.asyncio
    async def test_create_update_publish_creates_pending_bot_record(
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
        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="ACTIVE",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
            config=PublishConfig(
                deploy_config={"after_create_cmd_hook": "/scripts/test.sh"},
            ),
        )
        created_publish_ids.append(publish.id)

        assert publish.status == PublishStatus.PENDING.value

        # Verify target_bot_id in extra_config
        assert publish.extra_config is not None
        assert publish.extra_config.target_bot_id is not None
        target_bot_id = publish.extra_config.target_bot_id

        # Verify new bot record exists with PENDING status
        env = get_current_env()
        new_bot = bot_repository.get_by_id(target_bot_id, tenant_name, env)
        assert new_bot is not None
        assert new_bot.status == BotStatus.PENDING.value
        assert (
            new_bot.bot_uuid
            == bot_repository.get_by_id(bot_id, tenant_name, env).bot_uuid
        )

        # Clean up new bot record
        created_bot_ids.append(target_bot_id)

    @pytest.mark.asyncio
    async def test_create_update_publish_old_bot_remains_active(
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
        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="ACTIVE",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)

        # Old bot should still be ACTIVE
        env = get_current_env()
        old_bot = bot_repository.get_by_id(bot_id, tenant_name, env)
        assert old_bot is not None
        assert old_bot.status == BotStatus.ACTIVE.value


@pytest.mark.integration
class TestUpdatePublishComplete:
    """Integration tests for UPDATE publish completion with relationship transfer."""

    @pytest.mark.asyncio
    async def test_complete_update_transfers_relationships(
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
        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )
        env = get_current_env()

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        rel_repo = get_container().repository.bot_device_rel_repository()
        devices = create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repo,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="ACTIVE",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )
        device_uuid = devices[0]["device_uuid"]

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)
        assert publish.extra_config is not None
        target_bot_id = publish.extra_config.target_bot_id
        assert target_bot_id is not None
        created_bot_ids.append(target_bot_id)

        # Complete the publish
        result = await _ps().complete_publish(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_user",
        )
        assert result.status == PublishStatus.SUCCESS.value

        # Verify old bot is RELEASED and soft-deleted
        old_bot = bot_repository.get_by_id_including_deleted(bot_id, tenant_name, env)
        assert old_bot is not None
        assert old_bot.status == BotStatus.RELEASED.value
        assert old_bot.is_deleted != 0

        # Verify new bot is ACTIVE
        new_bot = bot_repository.get_by_id(target_bot_id, tenant_name, env)
        assert new_bot is not None
        assert new_bot.status == BotStatus.ACTIVE.value

        # Verify old rels are soft-deleted
        old_rels = rel_repo.list_by_bot_id(bot_id=bot_id, tenant=tenant_name, env=env)
        assert len(old_rels) == 0

        # Verify new rels exist for new bot
        new_rels = rel_repo.list_by_bot_id(
            bot_id=target_bot_id, tenant=tenant_name, env=env
        )
        assert len(new_rels) == 1
        assert new_rels[0].device_uuid == device_uuid

    @pytest.mark.asyncio
    async def test_complete_update_idempotent(
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
        from .conftest import (
            create_test_bot_record,
            create_test_devices_for_bot,
            create_test_tenant,
        )

        tenant_name, template_id = create_test_tenant(
            created_tenant_ids, created_template_ids
        )

        bot_id = create_test_bot_record(
            bot_repository,
            tenant=tenant_name,
            status=BotStatus.ACTIVE.value,
            created_bot_ids=created_bot_ids,
        )

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant_name,
            bot_id=bot_id,
            device_status="ACTIVE",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )

        publish = await _ps().create_publish(
            tenant=tenant_name,
            bot_id=bot_id,
            publish_type=PublishType.UPDATE,
            operator="test_user",
            request_id=uuid4().hex,
        )
        created_publish_ids.append(publish.id)
        assert publish.extra_config is not None
        target_bot_id = publish.extra_config.target_bot_id
        assert target_bot_id is not None
        created_bot_ids.append(target_bot_id)

        # Complete once
        result1 = await _ps().complete_publish(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_user",
        )
        assert result1.status == PublishStatus.SUCCESS.value

        # Complete again (should return early)
        result2 = await _ps().complete_publish(
            tenant=tenant_name,
            publish_id=publish.id,
            operator="test_user",
        )
        assert result2.status == PublishStatus.SUCCESS.value
