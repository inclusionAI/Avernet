"""Test data factories for integration tests.

Factories provide one-step entity creation with sensible defaults and
explicit override support.

Usage:
    from tests.integration.factories import PublishFactory

    factory = PublishFactory(publish_repo, batch_repo, record_repo, env="dev")
    publish_id, batch_id, records = factory.create_publish_chain(
        tenant="test_tenant", num_records=3
    )
"""

from .bot_factory import BotFactory
from .publish_factory import PublishFactory
from .tenant_factory import TenantFactory

__all__ = ["BotFactory", "PublishFactory", "TenantFactory"]
