from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.service_bot.repository.models import BotPublishRecord
from agentclaw.community.core.service_bot.services.arka_image_pin import (
    ImagePinPersistenceError,
    ImagePinConfigError,
    ImagePolicyState,
    PublishImagePolicyResolver,
    RUNTIME_KIND_ARKA,
    RUNTIME_KIND_TECLAW,
    apply_default_image_to_ext,
    apply_image_pin_to_ext,
    apply_runtime_kind_to_ext,
    copy_image_policy_to_ext,
    overlay_image_pin_on_template_config,
    resolve_current_arka_image,
    resolve_publish_image_pin,
    resolve_publish_runtime_kind,
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


def test_resolve_current_image_uses_enabled_common_config():
    service = MagicMock()
    service.get_value.return_value = {"image": " registry.example/arka:v2 "}

    assert resolve_current_arka_image(service, env="pre") == "registry.example/arka:v2"
    service.get_value.assert_called_once_with(
        business_code="service_bot",
        param_code="sbot_pin_image",
        env="pre",
        default=None,
        only_enabled=True,
    )


def test_resolve_current_image_returns_none_for_disabled_or_missing_config():
    service = MagicMock()
    service.get_value.return_value = None

    assert resolve_current_arka_image(service, env="pre") is None


@pytest.mark.parametrize("value", [{}, {"image": ""}, "registry/arka:v2"])
def test_resolve_current_image_rejects_enabled_malformed_config(value):
    service = MagicMock()
    service.get_value.return_value = value

    with pytest.raises(ImagePinConfigError, match="has no valid image"):
        resolve_current_arka_image(service, env="pre")


def test_apply_pin_preserves_service_bot_config_and_clears_stale_pin():
    ext = {
        "service_bot_config": {"device_count": 3},
        "sbot_pin_image": True,
        "sbot_docker_image": "old:v1",
    }

    assert apply_image_pin_to_ext(ext, None) == {
        "service_bot_config": {"device_count": 3}
    }
    assert apply_image_pin_to_ext(ext, "new:v2") == {
        "service_bot_config": {"device_count": 3},
        "sbot_pin_image": True,
        "sbot_docker_image": "new:v2",
    }


def test_apply_default_preserves_unrelated_fields_and_clears_stale_pin():
    assert apply_default_image_to_ext(
        {
            "service_bot_config": {"device_count": 3},
            "sbot_pin_image": True,
            "sbot_docker_image": "old:v1",
        }
    ) == {
        "service_bot_config": {"device_count": 3},
        "sbot_use_default_image": True,
    }


def test_publish_copy_is_whitelisted_and_template_overlay_is_non_mutating():
    source = {
        "service_bot_config": {"device_count": 3},
        "sbot_pin_image": True,
        "sbot_docker_image": "arka:v2",
    }
    target = {"config_artifact": {"schema_version": 4}}
    template = {"image": "template:v1", "envs": {"A": "1"}}

    assert copy_image_policy_to_ext(source, target) == {
        "config_artifact": {"schema_version": 4},
        "sbot_pin_image": True,
        "sbot_docker_image": "arka:v2",
    }
    assert overlay_image_pin_on_template_config(template, source) == {
        "image": "arka:v2",
        "envs": {"A": "1"},
    }
    assert template["image"] == "template:v1"


def test_publish_copy_supports_default_and_clears_stale_target_policy():
    source = {"sbot_use_default_image": True, "unrelated": "not-copied"}
    target = {
        "config_artifact": {"schema_version": 4},
        "sbot_pin_image": True,
        "sbot_docker_image": "old:v1",
    }

    assert copy_image_policy_to_ext(source, target) == {
        "config_artifact": {"schema_version": 4},
        "sbot_use_default_image": True,
    }


def test_resolve_publish_image_pin_reads_only_publish_ext():
    record = _record({"sbot_pin_image": True, "sbot_docker_image": "arka:v2"})
    common_config = MagicMock()

    resolved = resolve_publish_image_pin(
        record, common_config_service=common_config
    )

    assert resolved.enabled is True
    assert resolved.state == ImagePolicyState.PINNED
    assert resolved.docker_image == "arka:v2"
    common_config.get_value.assert_not_called()


def test_resolve_default_publish_does_not_read_common_config():
    common_config = MagicMock()

    resolved = resolve_publish_image_pin(
        _record({"sbot_use_default_image": True, "other": 1}),
        common_config_service=common_config,
    )

    assert resolved.state == ImagePolicyState.DEFAULT
    assert resolved.docker_image is None
    common_config.get_value.assert_not_called()


def test_resolve_legacy_publish_with_disabled_config_stays_legacy():
    common_config = MagicMock()
    common_config.get_value.return_value = None
    persist = MagicMock()

    resolved = resolve_publish_image_pin(
        _record({"migration_path": "/build/v1"}),
        common_config_service=common_config,
        persist_ext=persist,
    )

    assert resolved.state == ImagePolicyState.LEGACY
    assert resolved.docker_image is None
    persist.assert_not_called()


def test_resolve_legacy_publish_snapshots_pin_before_use():
    common_config = MagicMock()
    common_config.get_value.return_value = {"image": "registry/arka:v2"}
    persist = MagicMock()
    record = _record({"migration_path": "/build/v1"})

    resolved = resolve_publish_image_pin(
        record,
        common_config_service=common_config,
        persist_ext=persist,
    )

    expected_ext = {
        "migration_path": "/build/v1",
        "sbot_pin_image": True,
        "sbot_docker_image": "registry/arka:v2",
    }
    assert resolved.state == ImagePolicyState.PINNED
    assert resolved.docker_image == "registry/arka:v2"
    persist.assert_called_once_with(expected_ext)
    assert record.ext == expected_ext


def test_resolve_rejects_pin_without_image():
    with pytest.raises(ImagePinConfigError, match="without a valid image"):
        resolve_publish_image_pin(_record({"sbot_pin_image": True}))


def test_resolve_rejects_dangling_image_policy_field():
    with pytest.raises(ImagePinConfigError, match="inconsistent image policy"):
        resolve_publish_image_pin(_record({"sbot_docker_image": "arka:v2"}))


def test_runtime_kind_prefers_publish_snapshot():
    record = _record({"sbot_runtime_kind": RUNTIME_KIND_TECLAW})
    binding_repo = MagicMock()

    assert (
        resolve_publish_runtime_kind(record, binding_repository=binding_repo)
        == RUNTIME_KIND_TECLAW
    )
    binding_repo.get_by_id.assert_not_called()


def test_runtime_kind_reads_teclaw_artifact_for_historical_publish():
    record = _record({"config_artifact": {"engine_type": "teclaw"}})

    assert resolve_publish_runtime_kind(record) == RUNTIME_KIND_TECLAW


def test_runtime_kind_reads_string_stage_binding_provider():
    record = _record({"binding": {"online": "42"}})
    binding_repo = MagicMock()
    binding_repo.get_by_id.return_value = SimpleNamespace(device_provider="baas")

    assert (
        resolve_publish_runtime_kind(record, binding_repository=binding_repo)
        == RUNTIME_KIND_ARKA
    )
    binding_repo.get_by_id.assert_called_once_with(42)


def test_apply_runtime_kind_preserves_unrelated_ext():
    assert apply_runtime_kind_to_ext({"migration_path": "/build/v1"}, "arka") == {
        "migration_path": "/build/v1",
        "sbot_runtime_kind": "arka",
    }


def test_persisted_resolver_returns_successful_cas_snapshot():
    legacy = _record({"migration_path": "/build/v1", "unrelated": "keep"})
    persisted = _record(
        {
            "migration_path": "/build/v1",
            "unrelated": "keep",
            "sbot_pin_image": True,
            "sbot_docker_image": "registry/arka:v2",
        }
    )
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = legacy
    publish_repo.compare_and_set_ext.return_value = persisted
    common_config = MagicMock()
    common_config.get_value.return_value = {"image": "registry/arka:v2"}
    resolver = PublishImagePolicyResolver(
        publish_repository=publish_repo,
        binding_repository=MagicMock(),
        common_config_service=common_config,
    )
    original = _record()

    resolved = resolver.resolve(original)

    assert resolved.state == ImagePolicyState.PINNED
    assert resolved.docker_image == "registry/arka:v2"
    assert original.ext == persisted.ext
    publish_repo.compare_and_set_ext.assert_called_once_with(
        publish_id=legacy.id,
        expected_ext=legacy.ext,
        ext=persisted.ext,
    )


def test_persisted_resolver_reloads_concurrent_default_after_cas_conflict():
    legacy = _record({"migration_path": "/build/v1"})
    concurrent_default = _record(
        {"migration_path": "/build/v1", "sbot_use_default_image": True}
    )
    publish_repo = MagicMock()
    publish_repo.get_by_id.side_effect = [legacy, concurrent_default]
    publish_repo.compare_and_set_ext.return_value = None
    common_config = MagicMock()
    common_config.get_value.return_value = {"image": "registry/arka:v2"}
    resolver = PublishImagePolicyResolver(
        publish_repository=publish_repo,
        binding_repository=MagicMock(),
        common_config_service=common_config,
    )
    original = _record()

    resolved = resolver.resolve(original)

    assert resolved.state == ImagePolicyState.DEFAULT
    assert resolved.docker_image is None
    assert original.ext == concurrent_default.ext


def test_persisted_resolver_fails_closed_after_repeated_cas_conflicts():
    legacy = _record({"migration_path": "/build/v1"})
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = legacy
    publish_repo.compare_and_set_ext.return_value = None
    common_config = MagicMock()
    common_config.get_value.return_value = {"image": "registry/arka:v2"}
    resolver = PublishImagePolicyResolver(
        publish_repository=publish_repo,
        binding_repository=MagicMock(),
        common_config_service=common_config,
        max_cas_attempts=2,
    )

    with pytest.raises(ImagePinPersistenceError, match="CAS conflicted repeatedly"):
        resolver.resolve(_record())

    assert publish_repo.compare_and_set_ext.call_count == 2


def test_persisted_resolver_skips_common_config_for_teclaw_publish():
    latest = _record({"sbot_runtime_kind": "teclaw"})
    publish_repo = MagicMock()
    publish_repo.get_by_id.return_value = latest
    common_config = MagicMock()
    resolver = PublishImagePolicyResolver(
        publish_repository=publish_repo,
        binding_repository=MagicMock(),
        common_config_service=common_config,
    )

    resolved = resolver.resolve(_record())

    assert resolved.state == ImagePolicyState.LEGACY
    assert resolved.docker_image is None
    common_config.get_value.assert_not_called()
    publish_repo.compare_and_set_ext.assert_not_called()
