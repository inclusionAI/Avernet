"""Test-only DI: keep business, persistence, queue and outbound BCS real."""
from injector import Injector, Module, provider, singleton

from agentclaw.community.api.bot_service import BotServiceProtocol
from agentclaw.community.core.access.errors import UserNotFoundError
from agentclaw.community.core.access.user_service_protocol import UserServiceProtocol
from agentclaw.community.core.bot_management.owner_handoff.contracts import OwnerHandoffPolicy
from agentclaw.community.core.execution_identity.protocols import RuntimePassportTokenUpdaterProtocol
from agentclaw.community.di.bot_manager_sync_config import ManagerSyncConfig, parse_manager_sync_config
from agentclaw.community.di.config import TaskQueueWorkerConfig
from agentclaw.community.di.modules.config_module import read_user_config
from agentclaw.community.di.modules.owner_handoff_module import parse_handoff_policy
from agentclaw.community.plugin_api.passport import PassportPlugin
from agentclaw.community.plugin_api.staff_dept import StaffDeptPlugin, UserIdentityInfo
from agentclaw.community.plugins.local.passport import LocalPassportPlugin
from agentclaw.community.plugins.local.staff_dept import LocalStaffDeptService
from tests.community.acceptance.bot_ownership_sync.fixture_passport import PREFIX, FixturePassport
from tests.community.acceptance.bot_ownership_sync.fixture_proxy import OwnershipFaultProxy


class FixtureDirectory:
    def __init__(self, users, delegate):
        self._users, self._delegate = users, delegate

    def __getattr__(self, name):
        return getattr(self._delegate, name)

    def get_user_by_work_no(self, *, work_no):
        if not work_no.startswith(PREFIX):
            return self._delegate.get_user_by_work_no(work_no=work_no)
        try:
            user = self._users.get_user(user_id=work_no, user_type="NORMAL")
        except UserNotFoundError:
            return UserIdentityInfo(work_no=work_no)
        if user.user_id != work_no or user.status != "ACCESS":
            return UserIdentityInfo(work_no=work_no)
        return UserIdentityInfo(work_no=work_no, display_name=work_no)


class FixtureRuntime:
    def __init__(self, injector):
        self._injector = injector

    def hot_update_passport_token_to_device(self, bot_id, user_id, token):
        if bot_id.startswith(PREFIX) and user_id.startswith(PREFIX):
            return {"bindings": [{"device_count": 1}]}
        return self._injector.get(BotServiceProtocol).hot_update_passport_token_to_device(
            bot_id=bot_id, user_id=user_id, token=token,
        )


class OwnerHandoffFixtureModule(Module):
    def __init__(self, *, enable_fault_proxy=True):
        self._enable_fault_proxy = enable_fault_proxy

    @singleton
    @provider
    def fault_proxy(self) -> OwnershipFaultProxy:
        config = parse_manager_sync_config(read_user_config().get("bot_manager_sync"))
        return OwnershipFaultProxy(config.bcs_base_url)

    @singleton
    @provider
    def passport(self) -> PassportPlugin:
        return FixturePassport(LocalPassportPlugin())

    @singleton
    @provider
    def directory(self, users: UserServiceProtocol) -> StaffDeptPlugin:
        return FixtureDirectory(users, LocalStaffDeptService())

    @singleton
    @provider
    def runtime(self, injector: Injector) -> RuntimePassportTokenUpdaterProtocol:
        return FixtureRuntime(injector)

    @singleton
    @provider
    def handoff_policy(self, queue: TaskQueueWorkerConfig) -> OwnerHandoffPolicy:
        return parse_handoff_policy({"enabled": True, "recovery_enabled": True}, queue.enabled)

    @singleton
    @provider
    def sync_config(self) -> ManagerSyncConfig:
        block = dict(read_user_config().get("bot_manager_sync", {}))
        block["ownership_enabled"] = True
        config = parse_manager_sync_config(block)
        if self._enable_fault_proxy:
            block["bcs_base_url"] = self.__injector__.get(OwnershipFaultProxy).origin
            return parse_manager_sync_config(block)
        return config
