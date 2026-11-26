"""The backend forwards its publish stage to BaaS for ChangeFlow auditing."""

from agentclaw.community.core.service_bot.services.baas_service import (
    BaasService,
    BotConfig,
)


def _svc() -> BaasService:
    svc = object.__new__(BaasService)
    svc._default_ttl_minutes = 60
    svc._tenant = "t"
    return svc


def test_bot_config_serializes_publish_stage() -> None:
    config = BotConfig(publish_stage="online")
    assert config.to_dict()["publish_stage"] == "online"


def test_teclaw_payload_carries_publish_stage() -> None:
    payload = _svc()._build_teclaw_payload(
        {"entity_id": "e", "entity_type": "staff", "bot_name": "n"},
        "owner",
        "req-1",
        {"artifact": True},
        template_uuid="tmpl",
        device_count=1,
        ttl_in_minutes=None,
        publish_stage="online",
    )
    assert payload["config"]["publish_stage"] == "online"
