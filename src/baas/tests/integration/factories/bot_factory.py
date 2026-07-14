"""Factory for bot, device, and relationship entities."""

from typing import Any

from .base import DEFAULT_TEST_CREATOR, DEFAULT_TEST_DOMAIN, DEFAULT_TEST_MODIFIER


class BotFactory:
    """Build bot, device, and relationship records with minimal boilerplate."""

    def __init__(
        self,
        bot_repo: Any,
        device_repo: Any,
        rel_repo: Any,
        env: str,
    ) -> None:
        self.bot_repo = bot_repo
        self.device_repo = device_repo
        self.rel_repo = rel_repo
        self.env = env

    def create_bot(self, tenant: str, **overrides: Any) -> int:
        """Create a bot record and return its ID."""
        defaults: dict[str, Any] = {
            "bot_uuid": f"bot-test-{overrides.get('seed', 0):08d}",
            "tenant": tenant,
            "env": self.env,
            "domain": DEFAULT_TEST_DOMAIN,
            "creator": DEFAULT_TEST_CREATOR,
            "modifier": DEFAULT_TEST_MODIFIER,
            "status": "PENDING",
            "name": "Test Bot",
            "description": "Test bot for integration tests",
            "template_uuid": None,
            "replica_desired": 1,
            "replica_minimum": 1,
            "replica_maximum": 10,
            "auto_scaling_enabled": 0,
            "sla_grade": "standard",
            "extra_config": {},
        }
        defaults.update(overrides)
        return int(self.bot_repo.insert_bot(**defaults))

    def create_device(self, tenant: str, **overrides: Any) -> int:
        """Create a device record and return its ID."""
        defaults: dict[str, Any] = {
            "device_uuid": f"device-test-{overrides.get('seed', 0):08d}",
            "tenant": tenant,
            "env": self.env,
            "domain": DEFAULT_TEST_DOMAIN,
            "creator": DEFAULT_TEST_CREATOR,
            "modifier": DEFAULT_TEST_MODIFIER,
            "status": "ACTIVE",
            "provider_type": "SIGMA",
            "provider_device_id": None,
            "provider_device_props": {},
            "extra_config": {},
        }
        defaults.update(overrides)
        return int(self.device_repo.insert_device(**defaults))

    def create_bot_with_device(
        self, tenant: str, **overrides: Any
    ) -> tuple[int, int, int]:
        """Create a bot, a device, and link them.

        Args:
            tenant: Tenant name.
            **overrides: Optional overrides keyed by entity type::
                - ``bot``: dict forwarded to ``create_bot``
                - ``device``: dict forwarded to ``create_device``
                - ``rel``: dict forwarded to ``insert_rel``

        Returns:
            ``(bot_id, device_id, rel_id)``
        """
        bot_ov: dict[str, Any] = overrides.pop("bot", {})
        device_ov: dict[str, Any] = overrides.pop("device", {})
        rel_ov: dict[str, Any] = overrides.pop("rel", {})

        bot_id = self.create_bot(tenant, **bot_ov)
        device_id = self.create_device(tenant, **device_ov)
        device = self.device_repo.get_by_id(device_id, tenant, self.env)
        if device is None:
            raise RuntimeError("Device was not found immediately after creation")

        rel_id = self.rel_repo.insert_rel(
            bot_id=bot_id,
            device_uuid=device.device_uuid,
            tenant=tenant,
            env=self.env,
            domain=DEFAULT_TEST_DOMAIN,
            creator=DEFAULT_TEST_CREATOR,
            modifier=DEFAULT_TEST_MODIFIER,
            **rel_ov,
        )
        return bot_id, device_id, rel_id
