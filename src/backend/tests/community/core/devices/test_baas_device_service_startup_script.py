"""The stored startup script reaches the create payload (issue #926).

Covers the seam between the script store and ``_build_create_bot_payload`` — the
lookup itself, its failure mode, and the fact that both bot types travel the
same path.
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.devices.services.baas_device_service import (
    BaasDeviceService,
)


def _service(startup_script_service=None) -> BaasDeviceService:
    return BaasDeviceService(
        repository=MagicMock(),
        baas_service=MagicMock(),
        bot_query=MagicMock(),
        bot_sync=MagicMock(),
        oss_record_repo=MagicMock(),
        mcp_sync=MagicMock(),
        template_resolver=MagicMock(),
        startup_script_service=startup_script_service,
    )


class TestResolveStartupScript:
    def test_returns_the_stored_body(self):
        store = MagicMock()
        store.get_body.return_value = "echo provisioning"
        svc = _service(store)

        assert (
            svc._resolve_startup_script(entity_id="ent-1", bot_id="bot-1")
            == "echo provisioning"
        )
        store.get_body.assert_called_once_with(entity_id="ent-1", bot_id="bot-1")

    def test_returns_empty_string_when_the_bot_has_no_script(self):
        store = MagicMock()
        store.get_body.return_value = ""
        assert _service(store)._resolve_startup_script(
            entity_id="ent-1", bot_id="bot-1"
        ) == ""

    def test_returns_empty_string_when_the_service_is_not_bound(self):
        """A deployment without the binding composes exactly today's chain."""
        assert _service(None)._resolve_startup_script(
            entity_id="ent-1", bot_id="bot-1"
        ) == ""

    def test_storage_failure_does_not_block_provisioning(self):
        """Losing the script for one create beats failing the create.

        The bot picks it up on the next restart; a raised exception here would
        instead leave the caller with no container at all.
        """
        store = MagicMock()
        store.get_body.side_effect = RuntimeError("db down")

        assert _service(store)._resolve_startup_script(
            entity_id="ent-1", bot_id="bot-1"
        ) == ""

    def test_lookup_is_keyed_by_entity_and_bot(self):
        """entity_id is the storage key; it must not be dropped on the way in."""
        store = MagicMock()
        store.get_body.return_value = ""
        _service(store)._resolve_startup_script(entity_id="ent-9", bot_id="bot-9")

        kwargs = store.get_body.call_args.kwargs
        assert kwargs == {"entity_id": "ent-9", "bot_id": "bot-9"}


class TestPayloadCarriesTheScript:
    @pytest.mark.parametrize("bot_type", ["personal", "service"])
    def test_both_bot_types_pass_the_script_to_the_payload_builder(self, bot_type):
        """Personal and service bots share one allocator; only `stage` differs."""
        store = MagicMock()
        store.get_body.return_value = "echo hi"
        svc = _service(store)
        svc._template_resolver.resolve_template.return_value = MagicMock(
            template_uuid="tpl-uuid"
        )
        svc._lifecycle_executor = MagicMock()
        svc._lifecycle_executor.create_bot_from_payload.return_value = MagicMock(
            bot_uuid="uuid", publish_id=1, device_props={}
        )
        svc._baas_service._build_create_bot_payload.return_value = {}

        try:
            svc._allocate_via_baas(
                entity_id="ent-1",
                entity_type="staff",
                bolt_id="bot-1",
                device_id="dev-1",
                env="dev",
                engine="openclaw",
                bot_type=bot_type,
                owner_id="owner-1",
                bot_name="bot",
                bot_desc=None,
                extra_envs=None,
                template_type=None,
                template_config={"template_uid": "uid-1"},
            )
        except Exception:
            # The call continues past the payload build into BaaS I/O this test
            # does not stub; the assertion below is on what was composed.
            pass

        kwargs = svc._baas_service._build_create_bot_payload.call_args.kwargs
        assert kwargs["startup_script"] == "echo hi"
        # And the one documented difference between the two types is preserved.
        assert ("stage" in kwargs) is (bot_type == "service")
