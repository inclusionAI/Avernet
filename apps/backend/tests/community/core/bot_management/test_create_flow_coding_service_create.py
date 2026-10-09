"""Coding create-as-service: template carries build service bots directly.

A coding create (``engine_properties`` present) with ``bot_type="service"``
goes straight to the service create — the engine-strategy combination gate
admits service for factory-snapshot template carries, so the create is one
BaaS call (service-shaped container, BCN registration, draft publish record)
with no follow-up upgrade. The retired translation (personal create + seam
upgrade) raced the BaaS bot provisioning and left half-converted bots.

Hand-written ``applicationCoding`` engine properties keep the personal-only
shape: their workspace-hosting support (allocation + soft-delete rollback)
was only ever built for the personal form (#1403 first phase), so the
combination gate answers 409 for ``service`` there instead of silently
half-supporting it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.create_flow import (
    AuthPending,
    AuthStatus,
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    BotCreateTemplateValidationMode,
    Created,
    complete_bot_authorization,
    create_bot_with_authorization,
)
from agentclaw.community.core.bot_management.errors import (
    BotCombinationUnsupportedError,
)

pytestmark = pytest.mark.unit

_BOT_ID = "20260910_svce0001"


def _factory_snapshot_spec(bot_type: str = "service") -> BotCreateSpec:
    """Coding create through a template-factory snapshot (full identity)."""
    return BotCreateSpec(
        entity_id="85020",
        engine_type="claude_code",
        bot_type=bot_type,
        bot_name="factory服务创建",
        template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
        engine_properties={
            "template_type": "personalCoding",
            "template_config": {
                "template_key": "personalCoding",
                "template_uid": "aicoding_bot_template",
            },
        },
    )


def _legacy_application_coding_spec(bot_type: str = "service") -> BotCreateSpec:
    """Coding create with hand-written application-coding config."""
    return BotCreateSpec(
        entity_id="85020",
        engine_type="claude_code",
        bot_type=bot_type,
        bot_name="ac服务创建",
        template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
        engine_properties={
            "template_type": "applicationCoding",
            "template_config": {"token": "tok"},
        },
    )


def _bot_service(**overrides) -> MagicMock:
    service = MagicMock(spec=BotServiceProtocol)
    service.is_first_bot.return_value = False
    service.is_first_personal_bot.return_value = False
    service.is_workspace_hosting_available.return_value = True
    service.create_bot.return_value = {"bot_id": _BOT_ID, "bot_type": "service"}
    service.get_bot.return_value = {"bot_id": _BOT_ID, "bot_type": "service"}
    for name, value in overrides.items():
        setattr(service, name, value)
    return service


def _passport(issued: str | None = "tok") -> MagicMock:
    passport = MagicMock()
    passport.apply_agent_passport.return_value = {
        "token": issued,
        "agent_code": "ac-1",
        "iframe_url": "http://auth" if issued is None else None,
    }
    passport.query_auth_status.return_value = {"status": AuthStatus.ISSUED}
    passport.query_agent_passport.return_value = {"agent_code": "ac-1"}
    return passport


def _skill_set_factory() -> MagicMock:
    factory = MagicMock()
    factory.create.return_value.get_bot_mcp_codes.return_value = []
    return factory


def _cloud_context() -> BotCreateContext:
    return BotCreateContext(
        deployment_mode=BotCreateDeploymentMode.CLOUD,
        space_kind="personal",
    )


# ----- direct service creation ---------------------------------------------


def test_coding_service_create_builds_service_bot_directly():
    bot_service = _bot_service()

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_factory_snapshot_spec(bot_type="service"),
        context=_cloud_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
    )

    assert isinstance(outcome, Created)
    # One creation, already in the requested service shape — no translation,
    # no follow-up upgrade, hence no second BaaS call racing the first.
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "service"
    assert outcome.bot["bot_type"] == "service"


def test_coding_personal_create_unchanged():
    bot_service = _bot_service()
    bot_service.create_bot.return_value = {"bot_id": _BOT_ID, "bot_type": "personal"}

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_factory_snapshot_spec(bot_type="personal"),
        context=_cloud_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
    )

    assert isinstance(outcome, Created)
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "personal"


def test_plain_service_create_is_not_translated():
    bot_service = _bot_service()

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=BotCreateSpec(
            entity_id="85020",
            engine_type="openclaw",
            bot_type="service",
            bot_name="plain",
        ),
        context=_cloud_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
    )

    assert isinstance(outcome, Created)
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "service"


def test_coding_service_create_pending_does_not_create():
    bot_service = _bot_service()

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_factory_snapshot_spec(bot_type="service"),
        context=_cloud_context(),
        bot_service=bot_service,
        passport_plugin=_passport(issued=None),  # apply returns no token
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
    )

    assert isinstance(outcome, AuthPending)
    bot_service.create_bot.assert_not_called()


# ----- hand-written applicationCoding keeps the personal-only shape ---------


def test_legacy_application_coding_service_create_refused():
    with pytest.raises(BotCombinationUnsupportedError):
        create_bot_with_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id=_BOT_ID,
            spec=_legacy_application_coding_spec(bot_type="service"),
            context=_cloud_context(),
            bot_service=_bot_service(),
            passport_plugin=_passport(),
            auth_rel_plugin=MagicMock(),
            skill_set_factory=_skill_set_factory(),
        )


def test_legacy_application_coding_personal_create_unchanged():
    bot_service = _bot_service()
    bot_service.create_bot.return_value = {"bot_id": _BOT_ID, "bot_type": "personal"}

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_legacy_application_coding_spec(bot_type="personal"),
        context=_cloud_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
    )

    assert isinstance(outcome, Created)
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "personal"


# ----- ISSUED completion replays -------------------------------------------


def test_coding_service_create_completes_directly_on_issued():
    bot_service = _bot_service()

    result = complete_bot_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_factory_snapshot_spec(bot_type="service"),
        context=_cloud_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
    )

    assert result.status == AuthStatus.ISSUED
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "service"
    assert result.bot["bot_type"] == "service"


def test_coding_service_create_completion_refused_for_legacy():
    with pytest.raises(BotCombinationUnsupportedError):
        complete_bot_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id=_BOT_ID,
            spec=_legacy_application_coding_spec(bot_type="service"),
            context=_cloud_context(),
            bot_service=_bot_service(),
            passport_plugin=_passport(),
            auth_rel_plugin=MagicMock(),
        )


def test_pending_completion_does_not_create():
    passport = _passport()
    passport.query_auth_status.return_value = {"status": AuthStatus.PENDING}
    bot_service = _bot_service()

    result = complete_bot_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_factory_snapshot_spec(bot_type="service"),
        context=_cloud_context(),
        bot_service=bot_service,
        passport_plugin=passport,
        auth_rel_plugin=MagicMock(),
    )

    assert result.status == AuthStatus.PENDING
    bot_service.create_bot.assert_not_called()
