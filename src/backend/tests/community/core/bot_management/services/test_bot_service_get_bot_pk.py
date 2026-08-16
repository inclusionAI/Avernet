"""Unit tests for ``BotService.get_bot_pk``.

The narrow key read the engine-runtime relay makes on its published-stage
path, where it holds the resolved facts but no longer holds the bot row.
Covers the three outcomes its contract distinguishes:

- happy path: the row's ``ac_bots.id``, read owner-scoped.
- no live row -> ``BotNotFoundError`` (the bot is gone).
- a row whose key did not survive projection -> ``0``, deliberately *not* an
  exception, because ``resolve_stage_bind_id`` already owns that refusal.

Also pins that it stays narrow: unlike ``get_bot`` it must not reach for the
device binding or the template.

Constructed with MagicMock collaborators, matching the construction contract
in ``test_bot_service_list_bot_members``; only the ``repository`` seam is
driven.
"""
from unittest.mock import MagicMock

import pytest

from agentclaw.community.core.bot_management.services.bot_service import (
    BotNotFoundError,
    BotService,
)


def _make_bot_service(repository, device_service_provider, template_service) -> BotService:
    return BotService(
        drm_reader=MagicMock(),
        repository=repository,
        allocation_config=MagicMock(),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=device_service_provider,
        bot_app_grant_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=template_service,
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )


def _service(row):
    """A service whose owner-scoped lookup answers ``row``."""
    repository = MagicMock()
    repository.get_by_id_and_owner.return_value = row
    device_service_provider = MagicMock()
    template_service = MagicMock()
    service = _make_bot_service(repository, device_service_provider, template_service)
    return service, repository, device_service_provider, template_service


def test_returns_the_primary_key_of_the_owner_scoped_row():
    """The key comes from ``get_by_id_and_owner``, never a bare id lookup.

    ``ac_bots`` has no unique key on ``bot_id`` and every user's first bot is
    called ``default``, so a wider query could answer with another owner's row.
    """
    service, repository, _, _ = _service(
        {"bot_id": "default", "owner_id": "owner-1", "id": 100, "binding_id": 7}
    )

    assert service.get_bot_pk("default", "owner-1") == 100
    repository.get_by_id_and_owner.assert_called_once_with("default", "owner-1")


def test_stays_narrow_and_skips_the_binding_and_template_fetches():
    """The reason this is not ``get_bot``.

    ``get_bot`` also resolves the device binding (a provider call) and the
    template. A caller that wants an int should pay for neither — the relay
    makes this read on every published-stage forward.
    """
    service, _, device_service_provider, template_service = _service(
        {"bot_id": "default", "owner_id": "owner-1", "id": 100, "binding_id": 7}
    )

    service.get_bot_pk("default", "owner-1")

    device_service_provider.assert_not_called()
    template_service.get_template.assert_not_called()


def test_missing_row_raises_rather_than_answering_zero():
    """A bot that is gone is not a bot with an unreadable key.

    This is the one failure the relay's re-read adds over carrying the key on
    the facts, and it must stay distinguishable: folding it into "device not
    ready" would invite a retry of a bot that no longer exists.
    """
    service, _, _, _ = _service(None)

    with pytest.raises(BotNotFoundError):
        service.get_bot_pk("default", "owner-1")


def test_row_without_a_key_answers_zero_and_leaves_the_refusal_to_the_caller():
    """Deliberately not an exception.

    ``resolve_stage_bind_id`` already refuses a falsy key with
    ``DeviceNotBoundError`` before it queries anything, and that answer is
    caller-visible. Raising something else here would move it.
    """
    service, _, _, _ = _service({"bot_id": "default", "owner_id": "owner-1"})

    assert service.get_bot_pk("default", "owner-1") == 0
