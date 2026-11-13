"""The optional enterprise refresh port has explicit public DI wiring."""
from agentclaw.community.api.scheduled_caller_refresh import ScheduledCallerRefreshProtocol
from agentclaw.community.core.bot_management.services.bot_service import BotService
from agentclaw.community.core.caller_identity.scheduled_refresh import NoopScheduledCallerRefresh


def test_public_profile_wires_noop_into_owner_callback(test_injector):
    refresh = test_injector.get(ScheduledCallerRefreshProtocol)
    assert isinstance(refresh, NoopScheduledCallerRefresh)
    assert test_injector.get(BotService)._scheduled_caller_refresh is refresh
    assert refresh.refresh(bot_id="b", owner_user_id="o", entity_id="e", env="dev") is None


def test_public_runtime_ports_share_expiry_aware_updater(test_injector):
    from agentclaw.community.api.caller_credential import CallerRuntimeUpdater
    from agentclaw.community.core.caller_identity.expiry_runtime_updater import ExpiryAwareCallerRuntimeUpdater
    from agentclaw.community.core.caller_identity.protocols import CallerRuntimeUpdaterProtocol

    updater = test_injector.get(CallerRuntimeUpdater)
    assert isinstance(updater, ExpiryAwareCallerRuntimeUpdater)
    assert test_injector.get(CallerRuntimeUpdaterProtocol) is updater
