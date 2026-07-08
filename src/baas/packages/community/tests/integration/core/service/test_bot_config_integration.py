"""Integration tests for BotConfig/DeployConfig persistence.

Tests the full flow:
1. BotConfig -> bot.extra_config -> retrieve -> verify
2. PublishConfig -> publish.extra_config -> retrieve -> verify
"""

import uuid

import pytest

from secbaas.api.bot_manage import BotConfig
from secbaas.api.device_manage import DeployConfig
from secbaas.api.publish_manage import PublishConfig, PublishType
from secbaas.bootstrap import get_container
from secbaas.core.utils.env_utils import get_current_env


def _ps():
    return get_container().services.publish_service()


from .conftest import (
    create_test_bot_config,
    create_test_bot_deploy_config,
    create_test_devices_for_bot,
    create_test_publish_config,
    create_test_tenant,
    generate_unique_bot_uuid,
)

TEST_ENV = get_current_env()


# === Fixtures ===


@pytest.fixture
def test_setup(
    created_bot_ids,
    created_tenant_ids,
    created_template_ids,
    skip_if_zdas_unavailable,
):
    """Create test tenant and template for config tests."""
    tenant_name, template_id = create_test_tenant(
        created_tenant_ids, created_template_ids
    )
    # Ensure the template used in tests is ONLINE for lookups
    # Fetch the template_uuid via lookup since create_test_tenant doesn't return it
    try:
        template_repo = get_container().repository.device_template_repository()
        tpl_record = template_repo.get_by_id(template_id, tenant_name)
        if tpl_record:
            template_repo.update_status(
                template_uuid=tpl_record.template_uuid,
                tenant=tenant_name,
                current_status="",
                new_status="ONLINE",
            )
    except Exception:
        pass
    return {"tenant": tenant_name, "template_id": template_id}


# === Section 4: Dedicated BotConfig Persistence Tests ===


@pytest.mark.integration
class TestBotConfigPersistence:
    """Tests for BotConfig persistence through service layer."""

    def test_create_bot_with_full_deploy_config(
        self, test_setup, bot_repository, created_bot_ids
    ):
        """Test 4.1: BotConfig with deploy_config persists through bot creation."""
        tenant = test_setup["tenant"]

        # Create bot with BotConfig containing deploy_config
        deploy_config = create_test_bot_deploy_config()
        bot_config = create_test_bot_config(deploy_config=deploy_config)

        bot_uuid = generate_unique_bot_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            name=f"Config Test Bot {bot_uuid[:8]}",
            description="Bot for config persistence test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config=bot_config.model_dump(exclude_none=True),
        )
        created_bot_ids.append(bot_id)

        # Retrieve and verify
        record = bot_repository.get_by_id(bot_id, tenant=tenant, env=TEST_ENV)
        assert record is not None, "Bot should be retrievable"

        # Verify extra_config contains deploy_config
        retrieved_config = BotConfig.model_validate(record.extra_config)
        assert retrieved_config.deploy_config is not None
        assert (
            retrieved_config.deploy_config.after_create_cmd_hook
            == deploy_config.after_create_cmd_hook
        )
        assert (
            retrieved_config.deploy_config.before_destroy_cmd_hook
            == deploy_config.before_destroy_cmd_hook
        )

    def test_create_bot_with_minimal_config(
        self, test_setup, bot_repository, created_bot_ids
    ):
        """Test 4.2: BotConfig without deploy_config persists correctly."""
        tenant = test_setup["tenant"]

        # Create bot with minimal BotConfig (no deploy_config)
        bot_config = BotConfig(entity_id="staff_minimal", entity_type="staff")

        bot_uuid = generate_unique_bot_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            name=f"Minimal Config Bot {bot_uuid[:8]}",
            description="Bot for minimal config test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config=bot_config.model_dump(exclude_none=True),
        )
        created_bot_ids.append(bot_id)

        # Retrieve and verify
        record = bot_repository.get_by_id(bot_id, tenant=tenant, env=TEST_ENV)
        assert record is not None

        retrieved_config = BotConfig.model_validate(record.extra_config)
        assert retrieved_config.deploy_config is None
        assert retrieved_config.entity_id == "staff_minimal"
        assert retrieved_config.entity_type == "staff"

    def test_update_bot_partial_config_preserves_fields(
        self, test_setup, bot_repository, created_bot_ids
    ):
        """Test 4.3: Partial config update preserves other fields."""
        tenant = test_setup["tenant"]

        # Create bot with full config
        deploy_config = create_test_bot_deploy_config()
        bot_config = create_test_bot_config(deploy_config=deploy_config)

        bot_uuid = generate_unique_bot_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            name=f"Partial Update Bot {bot_uuid[:8]}",
            description="Bot for partial update test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config=bot_config.model_dump(exclude_none=True),
        )
        created_bot_ids.append(bot_id)

        # Update with partial config (only entity_id)
        partial_config = BotConfig(entity_id="staff_updated")

        # Simulate update logic from bot_management_service
        stored_config = BotConfig.model_validate(
            bot_repository.get_by_id(bot_id, tenant=tenant, env=TEST_ENV).extra_config
        )
        if partial_config.entity_id:
            stored_config.entity_id = partial_config.entity_id

        bot_repository.update_bot(
            bot_id=bot_id,
            tenant=tenant,
            env=TEST_ENV,
            extra_config=stored_config.model_dump(exclude_none=True),
            modifier="test_updater",
        )

        # Verify deploy_config is preserved
        record = bot_repository.get_by_id(bot_id, tenant=tenant, env=TEST_ENV)
        retrieved_config = BotConfig.model_validate(record.extra_config)

        assert retrieved_config.entity_id == "staff_updated"
        assert retrieved_config.deploy_config is not None
        assert (
            retrieved_config.deploy_config.after_create_cmd_hook
            == deploy_config.after_create_cmd_hook
        )

    @pytest.mark.asyncio
    async def test_publish_config_round_trip_with_nested_config(
        self,
        test_setup,
        publish_repository,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
    ):
        from secbaas.core.service.publish_manage import DefaultPublishService

        tenant = test_setup["tenant"]

        bot_uuid = generate_unique_bot_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            name=f"Publish Config Bot {bot_uuid[:8]}",
            description="Bot for publish config test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant,
            bot_id=bot_id,
            device_status="PENDING",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )
        deploy_config = create_test_bot_deploy_config()
        publish_config = create_test_publish_config(deploy_config=deploy_config)

        publish = await _ps().create_publish(
            tenant=tenant,
            bot_id=bot_id,
            publish_type=PublishType.CREATE,
            operator="test_user",
            request_id=uuid.uuid4().hex,
            config=publish_config,
        )
        created_publish_ids.append(publish.id)

        # Retrieve and verify
        record = publish_repository.get_by_id(publish.id, tenant=tenant, env=TEST_ENV)
        assert record is not None

        retrieved_config = PublishConfig.model_validate(record.extra_config)
        assert retrieved_config.deploy_config is not None
        assert (
            retrieved_config.deploy_config.after_create_cmd_hook
            == deploy_config.after_create_cmd_hook
        )
        assert (
            retrieved_config.deploy_config.before_destroy_cmd_hook
            == deploy_config.before_destroy_cmd_hook
        )


# === Section 6: Database Extra_config Round-trip Tests ===


@pytest.mark.integration
class TestDatabaseRoundTrip:
    """Tests for config serialization/deserialization through database."""

    def test_bot_config_round_trip_through_extra_config(
        self, test_setup, bot_repository, created_bot_ids
    ):
        """Test 6.1: BotConfig round-trip through baas_bot.extra_config."""
        tenant = test_setup["tenant"]

        # Create config with all fields
        original_config = BotConfig(
            share_policy={"public": True, "allowed_teams": ["team_a"]},
            deploy_config=DeployConfig(
                after_create_cmd_hook="/scripts/roundtrip.sh",
                after_create_hook_wait_seconds=60,
                envs={"ENV_KEY": "prod_value"},
            ),
            entity_id="staff_roundtrip",
            entity_type="staff",
        )

        bot_uuid = generate_unique_bot_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            name=f"Round Trip Bot {bot_uuid[:8]}",
            description="Bot for round trip test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config=original_config.model_dump(exclude_none=True),
        )
        created_bot_ids.append(bot_id)

        # Retrieve and deserialize
        record = bot_repository.get_by_id(bot_id, tenant=tenant, env=TEST_ENV)
        retrieved_config = BotConfig.model_validate(record.extra_config)

        # Verify all fields match
        assert retrieved_config.share_policy == original_config.share_policy
        assert retrieved_config.entity_id == original_config.entity_id
        assert retrieved_config.entity_type == original_config.entity_type
        rc_dc = retrieved_config.deploy_config
        oc_dc = original_config.deploy_config
        assert rc_dc is not None
        assert oc_dc is not None
        assert rc_dc.after_create_cmd_hook == oc_dc.after_create_cmd_hook
        assert rc_dc.envs == oc_dc.envs

    @pytest.mark.asyncio
    async def test_publish_config_round_trip_with_deploy_config(
        self,
        test_setup,
        publish_repository,
        bot_repository,
        device_repository,
        rel_repository,
        created_bot_ids,
        created_device_ids,
        created_rel_ids,
        created_publish_ids,
    ):
        from secbaas.core.service.publish_manage import DefaultPublishService

        tenant = test_setup["tenant"]

        bot_uuid = generate_unique_bot_uuid()
        bot_id = bot_repository.insert_bot(
            bot_uuid=bot_uuid,
            tenant=tenant,
            env=TEST_ENV,
            domain="test_domain",
            creator="test_user",
            modifier="test_user",
            status="ACTIVE",
            name=f"Publish Round Trip Bot {bot_uuid[:8]}",
            description="Bot for publish round trip test",
            template_uuid=None,
            replica_desired=1,
            replica_minimum=1,
            replica_maximum=10,
            auto_scaling_enabled=0,
            sla_grade="standard",
            extra_config={},
        )
        created_bot_ids.append(bot_id)

        create_test_devices_for_bot(
            device_repository=device_repository,
            rel_repository=rel_repository,
            tenant=tenant,
            bot_id=bot_id,
            device_status="PENDING",
            num_devices=1,
            created_device_ids=created_device_ids,
            created_rel_ids=created_rel_ids,
        )
        original_config = PublishConfig(
            bot_name="test_roundtrip_bot",
            replica_desired=5,
            batch_capacity=3,
            cooldown_seconds=0,
            deploy_config=DeployConfig(
                after_create_cmd_hook="/scripts/staging_setup.sh",
                before_destroy_cmd_hook="/scripts/staging_teardown.sh",
                envs={"ENVIRONMENT": "staging"},
            ),
        )

        publish = await _ps().create_publish(
            tenant=tenant,
            bot_id=bot_id,
            publish_type=PublishType.CREATE,
            operator="test_user",
            request_id=uuid.uuid4().hex,
            config=original_config,
        )
        created_publish_ids.append(publish.id)

        # Retrieve and deserialize
        record = publish_repository.get_by_id(publish.id, tenant=tenant, env=TEST_ENV)
        retrieved_config = PublishConfig.model_validate(record.extra_config)

        # Verify nested deploy_config is preserved
        assert retrieved_config.bot_name == original_config.bot_name
        assert retrieved_config.replica_desired == original_config.replica_desired
        rc_dc = retrieved_config.deploy_config
        oc_dc = original_config.deploy_config
        assert rc_dc is not None
        assert oc_dc is not None
        assert rc_dc.after_create_cmd_hook == oc_dc.after_create_cmd_hook
        assert rc_dc.envs == oc_dc.envs
