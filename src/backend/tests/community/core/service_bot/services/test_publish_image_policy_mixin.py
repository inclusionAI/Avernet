from datetime import datetime
from unittest.mock import MagicMock

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    ImagePolicyState,
)
from agentclaw.community.core.service_bot.services.publish_flow.image_policy_mixin import (
    PublishImagePolicyMixin,
)


def _record(ext=None) -> BotPublishRecord:
    return BotPublishRecord(
        id=1,
        source_bot_pk=1,
        source_bot_id="bot-1",
        publish_bot_id="bot-1",
        name="bot",
        owner_id="u1",
        status="success",
        version=2,
        env="pre",
        ext=ext,
        permission_owner="owner",
        gmt_create=datetime.now(),
        gmt_modified=datetime.now(),
    )


class _ImagePolicyHarness(PublishImagePolicyMixin):
    def __init__(self, *, latest=None, image=None):
        self._baas_service = MagicMock()
        self._baas_service.resolve_container_provider.return_value = "baas"
        self._common_config_service = MagicMock()
        self._common_config_service.get_value.return_value = (
            {"image": image} if image else None
        )
        self._publish_service = MagicMock()
        self._publish_service.get_publish_by_id.return_value = latest
        self.mutated_ext = None

    def _mutate_and_update_ext(self, publish_id, mutator):
        latest = self._publish_service.get_publish_by_id(publish_id)
        if latest is None:
            return None
        latest_ext = dict(latest.ext or {})
        mutator(latest_ext)
        latest.ext = latest_ext
        self.mutated_ext = latest_ext
        return latest_ext


def test_resolve_publish_image_pin_skips_teclaw():
    record = _record({"migration_path": "/build/v1"})
    svc = _ImagePolicyHarness(image="registry/arka:v2")
    svc._baas_service.resolve_container_provider.return_value = "teclaw"

    resolved = svc.resolve_publish_image_pin(record, {"active_engine": "teclaw"})

    assert resolved.state == ImagePolicyState.LEGACY
    assert resolved.docker_image is None
    svc._common_config_service.get_value.assert_not_called()


def test_resolve_publish_image_pin_persists_legacy_snapshot_and_refreshes_record():
    record = _record({"migration_path": "/build/v1"})
    latest = _record({"migration_path": "/build/v1", "unrelated": "preserved"})
    svc = _ImagePolicyHarness(latest=latest, image="registry/arka:v2")

    resolved = svc.resolve_publish_image_pin(record, {"active_engine": "openclaw"})

    assert resolved.state == ImagePolicyState.PINNED
    assert resolved.docker_image == "registry/arka:v2"
    assert svc.mutated_ext == {
        "migration_path": "/build/v1",
        "unrelated": "preserved",
        "sbot_pin_image": True,
        "sbot_docker_image": "registry/arka:v2",
    }
    assert record.ext == latest.ext


def test_resolve_publish_image_pin_does_not_replace_concurrent_explicit_policy():
    record = _record({"migration_path": "/build/v1"})
    latest = _record(
        {
            "migration_path": "/build/v1",
            "sbot_use_default_image": True,
        }
    )
    svc = _ImagePolicyHarness(latest=latest, image="registry/arka:v2")

    resolved = svc.resolve_publish_image_pin(record, {"active_engine": "openclaw"})

    assert resolved.state == ImagePolicyState.DEFAULT
    assert resolved.docker_image is None
    assert latest.ext == {
        "migration_path": "/build/v1",
        "sbot_use_default_image": True,
    }
    assert record.ext == latest.ext


def test_resolve_publish_image_pin_returns_initial_resolution_when_reread_missing():
    record = _record({"migration_path": "/build/v1"})
    svc = _ImagePolicyHarness(latest=None, image="registry/arka:v2")

    resolved = svc.resolve_publish_image_pin(record, {"active_engine": "openclaw"})

    assert resolved.state == ImagePolicyState.PINNED
    assert resolved.docker_image == "registry/arka:v2"
