"""Unit tests for ``AicodingProvisioningStrategy.refresh_restart_authorization``.

These mirror the create-time passport scope (``create_flow._apply_passport``)
so an ``aicoding`` / ``claude_code`` bot keeps the same MCP + CLI grants after a
restart. The strategy is opt-in: only when the caller passes
``extra_configs['confirmed_template_update']`` truthy does it recompute the
passport MCP codes + engine default CLI items and push them to Passport as a
full ``resource_scope`` snapshot (full replacement).
"""

from unittest.mock import MagicMock, patch

from agentclaw.community.core.bot_management.engines.aicoding.strategy import (
    AicodingProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.default import (
    DefaultProvisioningStrategy,
)
from agentclaw.community.core.bot_management.engines.provisioning import (
    BotProvisioningContext,
)

# Patch targets for the function-local imports inside
# ``refresh_restart_authorization`` (kept local to avoid the create_flow ->
# bot_service import cycle).
_MCP_CODES_PATH = "agentclaw.community.core.bot_management.create_flow._get_bot_mcp_codes"
_CLI_ITEMS_PATH = "agentclaw.community.core.mcp.services._defaults.get_default_cli_items"


def _ctx(active_engine="claude_code"):
    return BotProvisioningContext(
        bot_id="bot-1",
        owner_id="owner-1",
        bot_type="personal",
        active_engine=active_engine,
        template_type="architect",
    )


def _bot(entity_id="ent-1", entity_type="staff", bot_name="my-bot", bot_desc="d"):
    return {
        "entity_id": entity_id,
        "entity_type": entity_type,
        "bot_name": bot_name,
        "bot_desc": bot_desc,
    }


def _template_service(stored_config):
    service = MagicMock()
    service.get_template_config.return_value = stored_config
    return service


def _assert_skip(extra_configs):
    """Run refresh with ``extra_configs`` and assert a complete no-op.

    A no-op must not touch the template service, the MCP/CLI collectors, nor the
    passport plugin — the gate returns before any of them are touched.
    """
    template_service = _template_service({"template_key": "architect"})
    passport_plugin = MagicMock()
    skill_set_factory = MagicMock(name="skill_set_factory")
    strategy = AicodingProvisioningStrategy("claude_code")

    with patch(_MCP_CODES_PATH) as mcp_mock, patch(_CLI_ITEMS_PATH) as cli_mock:
        strategy.refresh_restart_authorization(
            _ctx(),
            _bot(),
            extra_configs,
            passport_plugin=passport_plugin,
            skill_set_factory=skill_set_factory,
            template_service=template_service,
        )
        template_service.get_template_config.assert_not_called()
        mcp_mock.assert_not_called()
        cli_mock.assert_not_called()
        passport_plugin.update_passport.assert_not_called()


# --------------------------------------------------------------------------- #
# Gate / no-op branch                                                         #
# --------------------------------------------------------------------------- #
def test_skip_when_extra_configs_is_none():
    _assert_skip(None)


def test_skip_when_extra_configs_is_empty_dict():
    _assert_skip({})


def test_skip_when_confirmed_template_update_key_missing():
    _assert_skip({"unrelated_key": True})


def test_skip_when_confirmed_template_update_is_false():
    _assert_skip({"confirmed_template_update": False})


def test_skip_when_confirmed_template_update_is_none():
    _assert_skip({"confirmed_template_update": None})


def test_skip_when_extra_configs_is_not_a_dict():
    # A non-dict envelope (str / list / bool) must also no-op without raising.
    for extra_configs in ("confirmed_template_update=true", ["confirmed"], True):
        _assert_skip(extra_configs)


# --------------------------------------------------------------------------- #
# Active branch: opt-in confirmed                                             #
# --------------------------------------------------------------------------- #
def _do_refresh(stored_config, active_engine="claude_code", strategy_engine="claude_code"):
    """Run refresh with the opt-in flag set; return all collaborators + mocks."""
    ctx = _ctx(active_engine=active_engine)
    bot = _bot()
    template_service = _template_service(stored_config)
    passport_plugin = MagicMock()
    skill_set_factory = MagicMock(name="skill_set_factory")
    strategy = AicodingProvisioningStrategy(strategy_engine)

    with patch(_MCP_CODES_PATH, return_value=["mcp-a", "mcp-b"]) as mcp_mock, \
            patch(_CLI_ITEMS_PATH, return_value=["cli-1"]) as cli_mock:
        strategy.refresh_restart_authorization(
            ctx,
            bot,
            {"confirmed_template_update": True},
            passport_plugin=passport_plugin,
            skill_set_factory=skill_set_factory,
            template_service=template_service,
        )
    return {
        "ctx": ctx,
        "bot": bot,
        "template_service": template_service,
        "passport_plugin": passport_plugin,
        "skill_set_factory": skill_set_factory,
        "mcp_mock": mcp_mock,
        "cli_mock": cli_mock,
    }


def test_active_updates_passport_with_full_resource_scope():
    r = _do_refresh({"template_key": "architect"})

    r["template_service"].get_template_config.assert_called_once_with("bot-1")
    r["mcp_mock"].assert_called_once_with(
        r["skill_set_factory"], "owner-1", "bot-1", "ent-1", "staff", "claude_code"
    )
    r["cli_mock"].assert_called_once_with(
        "claude_code", "architect",
        ext_info={"template_config": {"template_key": "architect"}},
    )
    r["passport_plugin"].update_passport.assert_called_once_with(
        bot_id="bot-1",
        user_id="owner-1",
        bot_name="my-bot",
        bot_desc="d",
        engine_type="claude_code",
        resource_scope={"mcp_codes": ["mcp-a", "mcp-b"], "cli_items": ["cli-1"]},
    )


def test_active_passes_ext_info_none_when_stored_config_is_none():
    # template_service.get_template_config -> None  =>  stored_config = {}
    r = _do_refresh(None)
    r["cli_mock"].assert_called_once_with(
        "claude_code", "architect", ext_info=None,
    )
    r["passport_plugin"].update_passport.assert_called_once()


def test_active_passes_ext_info_none_when_stored_config_is_empty_dict():
    r = _do_refresh({})
    r["cli_mock"].assert_called_once_with(
        "claude_code", "architect", ext_info=None,
    )
    r["passport_plugin"].update_passport.assert_called_once()


def test_active_uses_strategy_engine_type_when_ctx_active_engine_is_none():
    r = _do_refresh(
        {"template_key": "architect"},
        active_engine=None,
        strategy_engine="aicoding",
    )
    r["mcp_mock"].assert_called_once_with(
        r["skill_set_factory"], "owner-1", "bot-1", "ent-1", "staff", "aicoding"
    )
    r["cli_mock"].assert_called_once_with(
        "aicoding", "architect", ext_info={"template_config": {"template_key": "architect"}},
    )
    r["passport_plugin"].update_passport.assert_called_once_with(
        bot_id="bot-1",
        user_id="owner-1",
        bot_name="my-bot",
        bot_desc="d",
        engine_type="aicoding",
        resource_scope={"mcp_codes": ["mcp-a", "mcp-b"], "cli_items": ["cli-1"]},
    )


def test_active_defaults_entity_type_when_bot_missing_entity_type():
    bot = {"entity_id": "ent-9", "bot_name": "b", "bot_desc": "x"}  # no entity_type
    ctx = _ctx()
    template_service = _template_service({})
    passport_plugin = MagicMock()
    skill_set_factory = MagicMock(name="skill_set_factory")
    strategy = AicodingProvisioningStrategy("claude_code")

    with patch(_MCP_CODES_PATH, return_value=[]) as mcp_mock, \
            patch(_CLI_ITEMS_PATH, return_value=[]) as cli_mock:
        strategy.refresh_restart_authorization(
            ctx, bot, {"confirmed_template_update": True},
            passport_plugin=passport_plugin,
            skill_set_factory=skill_set_factory,
            template_service=template_service,
        )
    mcp_mock.assert_called_once_with(
        skill_set_factory, "owner-1", "bot-1", "ent-9", "staff", "claude_code"
    )
    passport_plugin.update_passport.assert_called_once_with(
        bot_id="bot-1",
        user_id="owner-1",
        bot_name="b",
        bot_desc="x",
        engine_type="claude_code",
        resource_scope={"mcp_codes": [], "cli_items": []},
    )


# --------------------------------------------------------------------------- #
# Failure propagation                                                         #
# --------------------------------------------------------------------------- #
def test_passport_failure_propagates_and_is_not_swallowed():
    """The strategy must let update_passport raise so the caller's try/except
    can log-and-continue; it must not swallow authorization errors itself."""

    ctx = _ctx()
    bot = _bot()
    template_service = _template_service({})
    passport_plugin = MagicMock()
    passport_plugin.update_passport.side_effect = RuntimeError("passport down")
    skill_set_factory = MagicMock(name="skill_set_factory")
    strategy = AicodingProvisioningStrategy("claude_code")

    with patch(_MCP_CODES_PATH, return_value=["mcp-a"]), \
            patch(_CLI_ITEMS_PATH, return_value=[]):
        raised = False
        try:
            strategy.refresh_restart_authorization(
                ctx, bot, {"confirmed_template_update": True},
                passport_plugin=passport_plugin,
                skill_set_factory=skill_set_factory,
                template_service=template_service,
            )
        except RuntimeError:
            raised = True
        assert raised, "update_passport failure must propagate to the caller"


# --------------------------------------------------------------------------- #
# Default engine strategy: no-op                                              #
# --------------------------------------------------------------------------- #
def test_default_strategy_no_op_does_not_touch_passport():
    template_service = MagicMock()
    passport_plugin = MagicMock()
    skill_set_factory = MagicMock(name="skill_set_factory")
    strategy = DefaultProvisioningStrategy("openclaw")

    # Even an opt-in request on a default engine must not authorize anything.
    strategy.refresh_restart_authorization(
        _ctx(),
        _bot(),
        {"confirmed_template_update": True},
        passport_plugin=passport_plugin,
        skill_set_factory=skill_set_factory,
        template_service=template_service,
    )
    passport_plugin.update_passport.assert_not_called()
    template_service.get_template_config.assert_not_called()


def test_default_strategy_no_op_returns_none():
    strategy = DefaultProvisioningStrategy("openclaw")
    result = strategy.refresh_restart_authorization(
        _ctx(),
        _bot(),
        None,
        passport_plugin=MagicMock(),
        skill_set_factory=MagicMock(),
        template_service=MagicMock(),
    )
    assert result is None
