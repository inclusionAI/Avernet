"""Sanity checks for factory layer.

These are not full integration tests — they verify that factories can be
instantiated and that default values are sensible.
"""

import pytest

from tests.integration.factories import BotFactory, PublishFactory, TenantFactory

pytestmark = pytest.mark.integration


class TestPublishFactorySanity:
    """Quick smoke tests for PublishFactory."""

    def test_factory_can_be_instantiated(
        self, publish_repository, publish_batch_repository, publish_record_repository
    ):
        factory = PublishFactory(
            publish_repository,
            publish_batch_repository,
            publish_record_repository,
            env="dev",
        )
        assert factory.publish_repo is publish_repository


class TestBotFactorySanity:
    """Quick smoke tests for BotFactory."""

    def test_factory_can_be_instantiated(
        self, bot_repository, device_repository, rel_repository
    ):
        factory = BotFactory(
            bot_repository, device_repository, rel_repository, env="dev"
        )
        assert factory.bot_repo is bot_repository


class TestTenantFactorySanity:
    """Quick smoke tests for TenantFactory."""

    def test_factory_can_be_instantiated(self, tenant_repository):
        factory = TenantFactory(tenant_repository, env="dev")
        assert factory.tenant_repo is tenant_repository
