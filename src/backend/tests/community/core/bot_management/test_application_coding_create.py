"""Application Coding creation policy and failure-path coverage.

The policy's single implementation is
``AicodingProvisioningStrategy.prepare_create``. ``create_flow._prepare_create``
routes both input shapes into it — the public ``engine_properties`` bag and the
legacy ``template_type="applicationCoding"`` pair normalized to
``{"template": config}`` — so the tests below cover the strategy directly plus
the flow's routing on top.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.bot_management.create_flow import (
    BotCreateContext,
    BotCreateDeploymentMode,
    BotCreateSpec,
    _prepare_create,
    complete_bot_authorization,
    create_bot_with_authorization,
)
from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.default import (
    DefaultProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotCreateTemplateValidationMode,
)
from agentclaw.community.core.bot_management.errors import (
    ApplicationCodingUnavailableError,
    BotCombinationUnsupportedError,
    BotTemplateInvalidError,
)
from agentclaw.community.core.bot_management.services.bot_service import (
    BotService,
    BotServiceError,
)

pytestmark = pytest.mark.unit

_CLOUD_PERSONAL = BotCreateContext(
    deployment_mode=BotCreateDeploymentMode.CLOUD,
    space_kind="personal",
)


def _strategy_prepare(
    engine_type: str = "claude_code",
    engine_properties: dict | None = None,
    bot_type: str = "personal",
    deployment_mode: str = "cloud",
    space_kind: str = "personal",
    template_validation_mode: BotCreateTemplateValidationMode = (
        BotCreateTemplateValidationMode.LEGACY
    ),
):
    return AicodingProvisioningStrategy(engine_type).prepare_create(
        engine_type=engine_type,
        engine_properties=engine_properties or {},
        bot_type=bot_type,
        deployment_mode=deployment_mode,
        space_kind=space_kind,
        template_validation_mode=template_validation_mode,
    )


def _prepare_spec(
    spec: BotCreateSpec,
    context: BotCreateContext = _CLOUD_PERSONAL,
    hosting_available: bool = True,
) -> BotCreateSpec:
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_workspace_hosting_available.return_value = hosting_available
    return _prepare_create(spec=spec, context=context, bot_service=bot_service)


def _application_coding_spec(**overrides) -> BotCreateSpec:
    params = dict(
        entity_id="u1",
        engine_type="claude_code",
        bot_type="personal",
        bot_name="Coding Bot",
        template_type="applicationCoding",
        template_config={"devflow_workflow": "x"},
    )
    params.update(overrides)
    return BotCreateSpec(**params)


# ── engine gate ────────────────────────────────────────────────────────────


def test_application_coding_engine_gate_reads_the_strategy_instance() -> None:
    # The aicoding strategy class is registered for both engine types, but
    # application-coding creation stays claude_code-only.
    prepared = _strategy_prepare(
        "claude_code", {"template": {"devflow_workflow": "x"}}
    )
    assert prepared.template_type == "applicationCoding"
    with pytest.raises(BotCombinationUnsupportedError):
        _strategy_prepare("aicoding", {"template": {"devflow_workflow": "x"}})


def test_default_engine_rejects_application_coding_as_combination_error() -> None:
    # openclaw/teclaw/hermes + applicationCoding historically answered 409
    # (BotCombinationUnsupportedError); routing by engine must keep that
    # mapping instead of turning it into a template-invalid 422.
    with pytest.raises(BotCombinationUnsupportedError):
        DefaultProvisioningStrategy("openclaw").prepare_create(
            engine_type="openclaw",
            engine_properties={"template": {"devflow_workflow": "x"}},
            bot_type="personal",
            deployment_mode="cloud",
            space_kind="personal",
        )


def test_default_engine_answers_cloud_only_before_the_engine_gate() -> None:
    # Historical gate order: the deleted prepare_bot_create checked the
    # deployment mode first, so a local deployment reports "cloud-only" even
    # when the engine gate would also reject.
    with pytest.raises(BotCombinationUnsupportedError, match="cloud-only"):
        DefaultProvisioningStrategy("openclaw").prepare_create(
            engine_type="openclaw",
            engine_properties={"template": {"devflow_workflow": "x"}},
            bot_type="personal",
            deployment_mode="local",
            space_kind="personal",
        )


def test_default_engine_rejects_other_engine_properties_keys() -> None:
    with pytest.raises(BotTemplateInvalidError):
        DefaultProvisioningStrategy("openclaw").prepare_create(
            engine_type="openclaw",
            engine_properties={"surprise": 1},
            bot_type="personal",
            deployment_mode="cloud",
            space_kind="personal",
        )


def test_unregistered_engine_is_named_in_the_combination_error() -> None:
    # Engines that pass the router's engine gate but have no registered
    # strategy (e.g. "moltis" in SUPPORTED_ENGINE_TYPES, or any free-form
    # engine_type on the untyped internal surface) resolve to the shared
    # default fallback. The error used to name "default"; it must name the
    # engine the caller actually asked for.
    with pytest.raises(BotCombinationUnsupportedError, match="moltis"):
        _prepare_spec(
            BotCreateSpec(
                entity_id="u1",
                engine_type="moltis",
                bot_type="personal",
                bot_name="Coding Bot",
                template_type="applicationCoding",
                template_config={"devflow_workflow": "x"},
            )
        )


def test_unknown_engine_properties_keys_are_rejected_by_the_strategy() -> None:
    # Core-level invariant: the HTTP schema's extra="forbid" cannot guard
    # direct spec construction by internal callers.
    with pytest.raises(BotTemplateInvalidError, match="unsupported"):
        _strategy_prepare("claude_code", {"template": {"a": 1}, "other": 2})


# ── strategy policy ────────────────────────────────────────────────────────


def test_prepare_plain_bot_has_no_hosting_requirement() -> None:
    prepared = _strategy_prepare()
    assert prepared.template_type is None
    assert prepared.template_config is None
    assert prepared.requires_workspace_hosting is False


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"engine_properties": {"template": {}}}, BotTemplateInvalidError),
        ({"engine_type": "aicoding"}, BotCombinationUnsupportedError),
        ({"bot_type": "service"}, BotCombinationUnsupportedError),
        ({"space_kind": "team"}, BotCombinationUnsupportedError),
        ({"deployment_mode": "local"}, BotCombinationUnsupportedError),
    ],
)
def test_prepare_rejects_invalid_application_coding_combinations(
    overrides, error
) -> None:
    params = dict(
        engine_type="claude_code",
        engine_properties={"template": {"devflow_workflow": "x"}},
        bot_type="personal",
        deployment_mode="cloud",
        space_kind="personal",
    )
    params.update(overrides)
    with pytest.raises(error):
        _strategy_prepare(**params)


def test_prepare_application_coding_without_config_preserves_legacy_default() -> None:
    # ``{"template": None}`` is the Core-only legacy compatibility shape: key
    # presence is the intent, None the intentionally-omitted config.
    prepared = _strategy_prepare("claude_code", {"template": None})
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config is None
    assert prepared.requires_workspace_hosting is True


def test_prepare_application_coding_rejects_supplied_empty_config() -> None:
    with pytest.raises(BotTemplateInvalidError, match="must not be empty"):
        _strategy_prepare("claude_code", {"template": {}})


def test_prepare_application_coding_rejects_known_field_with_wrong_type() -> None:
    with pytest.raises(BotTemplateInvalidError, match="code_repos"):
        _strategy_prepare("claude_code", {"template": {"code_repos": "not-a-list"}})


def test_prepare_valid_application_coding_returns_detached_config() -> None:
    payload = {"devflow_workflow": "x"}
    prepared = _strategy_prepare("claude_code", {"template": payload})
    assert prepared.template_config == payload
    assert prepared.template_config is not payload
    assert prepared.requires_workspace_hosting is True


def test_legacy_application_coding_preserves_template_uid() -> None:
    # Internal snapshots may carry platform-managed fields; the LEGACY mode
    # (the internal callers' default) must keep accepting them (#1442).
    payload = {"template_uid": "legacy-template", "devflow_workflow": "x"}
    prepared = _strategy_prepare("claude_code", {"template": payload})
    assert prepared.template_config == payload
    assert prepared.template_config is not payload
    assert prepared.requires_workspace_hosting is True


def test_legacy_non_dict_template_config_keeps_historical_passthrough() -> None:
    # The internal /api/bots surface forwards template_config untyped; the
    # pre-strategy ladder let truthy non-dict values through (only genuinely
    # empty payloads were rejected). The strategy keeps that contract instead
    # of tightening it into a rejection.
    prepared = _strategy_prepare("claude_code", {"template": "legacy-ref"})
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config == "legacy-ref"
    assert prepared.requires_workspace_hosting is True


def test_public_application_coding_rejects_template_uid() -> None:
    with pytest.raises(
        BotTemplateInvalidError,
        match="server-managed fields.*template_uid",
    ):
        _strategy_prepare(
            "claude_code",
            {"template": {"template_uid": "caller-value"}},
            template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
        )


def test_flow_preserves_legacy_template_uid_through_the_strategy() -> None:
    payload = {"template_uid": "legacy-template", "devflow_workflow": "x"}
    prepared = _prepare_spec(_application_coding_spec(template_config=payload))
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config == payload
    assert prepared.template_config is not payload


# ── create-flow source routing ─────────────────────────────────────────────


def test_flow_routes_both_input_shapes_through_the_same_strategy() -> None:
    legacy = _prepare_spec(_application_coding_spec())
    modern = _prepare_spec(
        BotCreateSpec(
            entity_id="u1",
            engine_type="claude_code",
            bot_type="personal",
            bot_name="Coding Bot",
            engine_properties={"template": {"devflow_workflow": "x"}},
        )
    )
    assert legacy.template_type == modern.template_type == "applicationCoding"
    assert legacy.template_config == modern.template_config
    # The legacy spec's bag stays untouched: normalization happens on the copy
    # handed to the strategy, never on the caller's spec.
    assert legacy.engine_properties == {}


def test_flow_output_satisfies_its_own_mixed_source_invariant() -> None:
    # The translated spec is what a retry (or the deferred pending-intent
    # replay) would re-feed into _prepare_create: it may carry the translated
    # template fields, so the consumed engine_properties bag must be cleared
    # rather than travelling alongside them as a second source.
    prepared = _prepare_spec(
        BotCreateSpec(
            entity_id="u1",
            engine_type="claude_code",
            bot_type="personal",
            bot_name="Coding Bot",
            engine_properties={"template": {"devflow_workflow": "x"}},
        )
    )
    assert prepared.template_type == "applicationCoding"
    assert prepared.engine_properties == {}
    # Re-prepare must not hit the mixed-source guard.
    roundtrip = _prepare_spec(prepared)
    assert roundtrip.template_type == "applicationCoding"
    assert roundtrip.template_config == {"devflow_workflow": "x"}


def test_flow_preserves_legacy_non_application_template() -> None:
    payload = {"legacy": {"enabled": True}}
    prepared = _prepare_spec(
        BotCreateSpec(
            entity_id="u1",
            engine_type="openclaw",
            bot_type="personal",
            bot_name="Bot",
            template_type="personalCoding",
            template_config=payload,
        )
    )
    assert prepared.template_type == "personalCoding"
    assert prepared.template_config == payload
    assert prepared.template_config is not payload


def test_flow_preserves_legacy_application_coding_without_config() -> None:
    prepared = _prepare_spec(
        _application_coding_spec(template_config=None),
    )
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config is None


def test_flow_rejects_mixed_template_sources() -> None:
    with pytest.raises(BotTemplateInvalidError):
        _prepare_spec(
            BotCreateSpec(
                entity_id="u1",
                engine_type="claude_code",
                bot_type="personal",
                bot_name="Coding Bot",
                template_type="applicationCoding",
                template_config={"devflow_workflow": "x"},
                engine_properties={"template": {"devflow_workflow": "x"}},
            )
        )


def test_shared_create_rejects_missing_hosting_before_passport() -> None:
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_workspace_hosting_available.return_value = False
    passport = MagicMock()

    with pytest.raises(ApplicationCodingUnavailableError):
        create_bot_with_authorization(
            user_id="u1",
            nick_name="u1",
            bot_id="b1",
            spec=_application_coding_spec(),
            context=_CLOUD_PERSONAL,
            bot_service=bot_service,
            passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
            skill_set_factory=MagicMock(),
        )

    passport.apply_first_agent_passport.assert_not_called()
    passport.apply_agent_passport.assert_not_called()
    bot_service.check_create_bot_preflight.assert_not_called()


def test_plain_bot_does_not_query_workspace_hosting() -> None:
    bot_service = MagicMock(spec=BotServiceProtocol)
    bot_service.is_first_bot.return_value = True
    passport = MagicMock()
    passport.apply_first_agent_passport.return_value = {
        "iframe_url": "https://passport/authorize"
    }
    skill_set_factory = MagicMock()
    skill_set_factory.create.return_value.get_bot_mcp_codes.return_value = []

    create_bot_with_authorization(
        user_id="u1",
        nick_name="u1",
        bot_id="b1",
        spec=BotCreateSpec(
            entity_id="u1",
            engine_type="openclaw",
            bot_type="personal",
            bot_name="Plain Bot",
        ),
        context=_CLOUD_PERSONAL,
        bot_service=bot_service,
        passport_plugin=passport,
        auth_rel_plugin=MagicMock(),
        skill_set_factory=skill_set_factory,
    )

    bot_service.is_workspace_hosting_available.assert_not_called()


def test_auth_completion_rejects_invalid_combo_before_passport_query() -> None:
    bot_service = MagicMock(spec=BotServiceProtocol)
    passport = MagicMock()

    with pytest.raises(BotCombinationUnsupportedError):
        complete_bot_authorization(
            user_id="u1",
            nick_name="u1",
            bot_id="b1",
            spec=_application_coding_spec(bot_type="service"),
            context=_CLOUD_PERSONAL,
            bot_service=bot_service,
            passport_plugin=passport,
            auth_rel_plugin=MagicMock(),
        )

    passport.query_auth_status.assert_not_called()


# ── create_bot workspace/template failure ────────────────────────────────


def _create_bot_service() -> BotService:
    service = BotService(
        caller_identity_repo=MagicMock(),
        drm_reader=MagicMock(),
        repository=MagicMock(),
        allocation_config=MagicMock(mode="multi", max_devices_per_entity=5),
        device_binding_repo=MagicMock(),
        skill_set_factory=MagicMock(),
        cleanup_service=MagicMock(),
        bcn_service=MagicMock(),
        bot_publish_repo=MagicMock(),
        passport_plugin=MagicMock(),
        oss_record_repo=MagicMock(),
        bot_publish_service_provider=lambda: MagicMock(),
        device_service_provider=lambda: MagicMock(),
        bot_app_grant_service_provider=lambda: MagicMock(),
        path_factory=MagicMock(),
        template_service=MagicMock(),
        workspace_hosting_service=MagicMock(),
        collaborator_repo=MagicMock(),
        restart_lock_repo=MagicMock(),
        teclaw_provision_service_provider=lambda: MagicMock(
            is_teclaw=MagicMock(return_value=False)
        ),
        device_status_client=MagicMock(),
        cron_auto_setup_service_provider=lambda: MagicMock(),
    )
    # Drive create_bot past the pre-device gates so it reaches Step 1.5.
    service.check_create_bot_preflight = MagicMock()
    service._check_device_limit = MagicMock()
    service._resolve_bot_name = MagicMock(return_value="app-coding-bot")
    service._repository.get_by_id_and_owner = MagicMock(return_value=None)
    service._repository.insert = MagicMock(
        return_value={"id": 1, "bot_id": "b1", "owner_id": "u1"}
    )
    service._repository.soft_delete_by_owner = MagicMock()
    return service


def _create(service: BotService) -> None:
    service.create_bot(
        user_id="u1",
        nick_name="n",
        bot_id="b1",
        bot_type="personal",
        engine_type="claude_code",
        template_type="applicationCoding",
        template_config={"devflow_workflow": "x"},
    )


def test_workspace_creation_exception_is_fatal() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_service.create_workspace_for_bot = MagicMock(
        side_effect=RuntimeError("boom")
    )
    with pytest.raises(BotServiceError):
        _create(svc)
    svc._repository.soft_delete_by_owner.assert_called_once_with("b1", "u1")


def test_workspace_creation_falsy_return_is_fatal() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_service.create_workspace_for_bot = MagicMock(
        return_value=None
    )
    with pytest.raises(BotServiceError):
        _create(svc)
    svc._repository.soft_delete_by_owner.assert_called_once_with("b1", "u1")


def test_template_creation_failure_is_fatal() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = MagicMock()
    svc._workspace_hosting_service.create_workspace_for_bot = MagicMock(
        return_value="ws-1"
    )
    svc._template_service.create_template = MagicMock(side_effect=RuntimeError("boom"))
    with pytest.raises(BotServiceError):
        _create(svc)
    svc._repository.soft_delete_by_owner.assert_called_once_with("b1", "u1")


def test_is_workspace_hosting_available() -> None:
    svc = _create_bot_service()
    svc._workspace_hosting_service = None
    assert svc.is_workspace_hosting_available() is False
    svc._workspace_hosting_service = MagicMock()
    assert svc.is_workspace_hosting_available() is True


# ── legacy engine alias folding (engine/form vocabulary split) ─────────────


def test_legacy_aicoding_engine_folds_into_claude_code_with_form_marker() -> None:
    """Old-link compat: engine_type="aicoding" + applicationCoding creates.

    Before the vocabulary split this combination was rejected by the strategy's
    claude_code-only gate; folding the alias first both restores the old link's
    behavior and records the server-managed form marker the runtime bucket
    routing reads.
    """
    prepared = _prepare_spec(
        _application_coding_spec(
            engine_type="aicoding", template_config={"devflow_workflow": "x"}
        )
    )
    assert prepared.engine_type == "claude_code"
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config == {
        "devflow_workflow": "x",
        "engine_form": "aicoding",
    }


def test_legacy_aicoding_plain_bot_folds_without_any_marker() -> None:
    """A plain no-template bot has no form: it is simply a claude_code bot."""
    prepared = _prepare_spec(
        BotCreateSpec(
            entity_id="u1",
            engine_type="aicoding",
            bot_type="personal",
            bot_name="Bot",
        )
    )
    assert prepared.engine_type == "claude_code"
    assert prepared.template_type is None
    assert prepared.template_config is None


def test_legacy_aicoding_without_template_config_writes_no_marker() -> None:
    """Legacy Core-only shape (template present, config intentionally None):
    nothing to merge the marker into; the existing template-type semantics
    (claude_code + applicationCoding → aicoding runtime) already route it."""
    prepared = _prepare_spec(
        _application_coding_spec(engine_type="aicoding", template_config=None)
    )
    assert prepared.engine_type == "claude_code"
    assert prepared.template_type == "applicationCoding"
    assert prepared.template_config is None


def test_engine_folding_is_idempotent_for_real_engines() -> None:
    spec = _application_coding_spec()  # engine_type="claude_code"
    prepared = _prepare_spec(spec)
    assert prepared.engine_type == "claude_code"
    assert prepared.template_config == {"devflow_workflow": "x"}
    # Re-prepare of the translated output keeps a stable single marker.
    second = _prepare_spec(
        _application_coding_spec(
            engine_type="aicoding",
            template_config={"devflow_workflow": "x", "engine_form": "aicoding"},
        )
    )
    assert second.template_config == {
        "devflow_workflow": "x",
        "engine_form": "aicoding",
    }


def test_public_input_cannot_smuggle_the_form_marker() -> None:
    """The marker is server-managed: PUBLIC validation rejects caller input."""
    with pytest.raises(BotTemplateInvalidError) as excinfo:
        _strategy_prepare(
            engine_properties={
                "template": {"devflow_workflow": "x", "engine_form": "aicoding"}
            },
            template_validation_mode=BotCreateTemplateValidationMode.PUBLIC,
        )
    assert "engine_form" in str(excinfo.value)
    # The legacy internal shape may carry it (platform-written snapshot).
    prepared = _strategy_prepare(
        engine_properties={
            "template": {"devflow_workflow": "x", "engine_form": "aicoding"}
        },
        template_validation_mode=BotCreateTemplateValidationMode.LEGACY,
    )
    assert prepared.template_config["engine_form"] == "aicoding"
