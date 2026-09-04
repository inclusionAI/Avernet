"""Service-intake translation: create-as-service coding bots via upgrade.

A coding create (``engine_properties`` present) is personal-only by the
engine-strategy combination gate. Surfaces that opt in via
``BotCreateContext.service_intake`` get the product's "开启服务" flow instead:
the spec is translated to ``personal`` so the same gate passes, and after the
bot is created + owned the flow drives the service upgrade through the
``ServiceIntakeSeam`` — the create-as-service the caller asked for, without
weakening the gate for callers that did not opt in.
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
    ServiceIntakeConversionError,
)

pytestmark = pytest.mark.unit

_BOT_ID = "20260904_svce0001"


def _coding_spec(bot_type: str = "personal") -> BotCreateSpec:
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
    service.create_bot.return_value = {"bot_id": _BOT_ID, "bot_type": "personal"}
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


def _intake_context() -> BotCreateContext:
    return BotCreateContext(
        deployment_mode=BotCreateDeploymentMode.CLOUD,
        space_kind="personal",
        service_intake=True,
    )


def _plain_context() -> BotCreateContext:
    return BotCreateContext(
        deployment_mode=BotCreateDeploymentMode.CLOUD,
        space_kind="personal",
    )


# ----- translation + inline creation ---------------------------------------


def test_service_intake_creates_personal_then_converts():
    seam = MagicMock()
    bot_service = _bot_service()

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_coding_spec(bot_type="service"),
        context=_intake_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
        service_intake_seam=seam,
    )

    assert isinstance(outcome, Created)
    # The strategy gate saw a personal create…
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "personal"
    # …then the freshly created bot was upgraded and the result re-read.
    seam.convert.assert_called_once_with(
        _BOT_ID, actor_id="85020", owner_id="85020"
    )
    assert bot_service.get_bot.call_args.args == (_BOT_ID, "85020")
    assert outcome.bot["bot_type"] == "service"


def test_service_intake_without_flag_keeps_the_combination_gate():
    with pytest.raises(BotCombinationUnsupportedError):
        create_bot_with_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id=_BOT_ID,
            spec=_coding_spec(bot_type="service"),
            context=_plain_context(),
            bot_service=_bot_service(),
            passport_plugin=_passport(),
            auth_rel_plugin=MagicMock(),
            skill_set_factory=_skill_set_factory(),
            service_intake_seam=MagicMock(),
        )


def test_service_intake_without_seam_keeps_the_combination_gate():
    """Opt-in without the upgraded seam wired is misconfiguration, not intent."""
    with pytest.raises(BotCombinationUnsupportedError):
        create_bot_with_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id=_BOT_ID,
            spec=_coding_spec(bot_type="service"),
            context=_intake_context(),
            bot_service=_bot_service(),
            passport_plugin=_passport(),
            auth_rel_plugin=MagicMock(),
            skill_set_factory=_skill_set_factory(),
        )


def test_plain_service_create_is_not_translated():
    """No engine_properties → no coding gate to bypass, no conversion to drive."""
    seam = MagicMock()
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
        context=_intake_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
        service_intake_seam=seam,
    )

    assert isinstance(outcome, Created)
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "service"
    seam.convert.assert_not_called()


def test_service_intake_conversion_failure_raises_with_bot_id():
    seam = MagicMock()
    seam.convert.side_effect = RuntimeError("bcn down")

    with pytest.raises(ServiceIntakeConversionError) as excinfo:
        create_bot_with_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id=_BOT_ID,
            spec=_coding_spec(bot_type="service"),
            context=_intake_context(),
            bot_service=_bot_service(),
            passport_plugin=_passport(),
            auth_rel_plugin=MagicMock(),
            skill_set_factory=_skill_set_factory(),
            service_intake_seam=seam,
        )

    # The bot was created (and stays personal); the failure names it so the
    # caller can retry the upgrade instead of re-creating.
    assert _BOT_ID in str(excinfo.value)


def test_service_intake_passport_pending_does_not_convert():
    """Nothing exists yet on a pending authorization — nothing to upgrade."""
    seam = MagicMock()
    bot_service = _bot_service()

    outcome = create_bot_with_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_coding_spec(bot_type="service"),
        context=_intake_context(),
        bot_service=bot_service,
        passport_plugin=_passport(issued=None),  # apply returns no token
        auth_rel_plugin=MagicMock(),
        skill_set_factory=_skill_set_factory(),
        service_intake_seam=seam,
    )

    assert isinstance(outcome, AuthPending)
    bot_service.create_bot.assert_not_called()
    seam.convert.assert_not_called()


# ----- ISSUED completion replays -------------------------------------------


def test_service_intake_completes_and_converts_on_issued():
    seam = MagicMock()
    bot_service = _bot_service()

    result = complete_bot_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_coding_spec(bot_type="service"),
        context=_intake_context(),
        bot_service=bot_service,
        passport_plugin=_passport(),
        auth_rel_plugin=MagicMock(),
        service_intake_seam=seam,
    )

    assert result.status == AuthStatus.ISSUED
    assert bot_service.create_bot.call_args.kwargs["bot_type"] == "personal"
    seam.convert.assert_called_once_with(
        _BOT_ID, actor_id="85020", owner_id="85020"
    )
    assert result.bot["bot_type"] == "service"


def test_service_intake_pending_completion_does_not_create_or_convert():
    passport = _passport()
    passport.query_auth_status.return_value = {"status": AuthStatus.PENDING}
    seam = MagicMock()
    bot_service = _bot_service()

    result = complete_bot_authorization(
        user_id="85020",
        nick_name="Alice",
        bot_id=_BOT_ID,
        spec=_coding_spec(bot_type="service"),
        context=_intake_context(),
        bot_service=bot_service,
        passport_plugin=passport,
        auth_rel_plugin=MagicMock(),
        service_intake_seam=seam,
    )

    assert result.status == AuthStatus.PENDING
    bot_service.create_bot.assert_not_called()
    seam.convert.assert_not_called()


def test_service_intake_completion_conversion_failure_names_bot_id():
    seam = MagicMock()
    seam.convert.side_effect = RuntimeError("publish unavailable")

    with pytest.raises(ServiceIntakeConversionError) as excinfo:
        complete_bot_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id=_BOT_ID,
            spec=_coding_spec(bot_type="service"),
            context=_intake_context(),
            bot_service=_bot_service(),
            passport_plugin=_passport(),
            auth_rel_plugin=MagicMock(),
            service_intake_seam=seam,
        )

    assert _BOT_ID in str(excinfo.value)


def test_service_intake_completion_without_gate_keeps_409():
    with pytest.raises(BotCombinationUnsupportedError):
        complete_bot_authorization(
            user_id="85020",
            nick_name="Alice",
            bot_id=_BOT_ID,
            spec=_coding_spec(bot_type="service"),
            context=_plain_context(),
            bot_service=_bot_service(),
            passport_plugin=_passport(),
            auth_rel_plugin=MagicMock(),
            service_intake_seam=MagicMock(),
        )
