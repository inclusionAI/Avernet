"""Unit tests for CollaboratorService 的 coding 应用放行分支。

覆盖暂存区变更：add_collaborator 中
    is_coding_app = active_engine == "claude_code" and template_type == "applicationCoding"
    if bot_type != "service" and not is_coding_app: raise BotNotServiceTypeError
即 coding 应用（Personal Bot）作为"应用成员"复用协作者流程时放行。
"""
import pytest
from unittest.mock import Mock

from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
    CollaboratorService,
    BotNotServiceTypeError,
    BotNotFoundError,
)
from agentclaw.community.core.bot_collaborator.models import (
    CollaboratorRecord,
    CollaboratorRole,
)


OWNER = "owner-001"
MEMBER = "user-002"


@pytest.fixture
def collaborator_repo():
    repo = Mock()
    repo.get_by_bot_and_user.return_value = None  # 尚未添加过
    repo.insert.return_value = Mock(name="record")
    return repo


@pytest.fixture
def bot_repo():
    return Mock()


@pytest.fixture
def template_service():
    svc = Mock()
    svc.get_template_config.return_value = None
    return svc


@pytest.fixture
def service(collaborator_repo, bot_repo, template_service):
    svc = CollaboratorService(
        collaborator_repo=collaborator_repo,
        bot_repo=bot_repo,
        passport_plugin=Mock(),
        resolver_provider=lambda: Mock(),
        device_fs_dispatcher_provider=lambda: Mock(),
        template_service=template_service,
    )
    # 隔离协作变更回调副作用（会反查协作者列表 / AgentPass），聚焦类型判断分支
    svc.on_collaboration_changed = Mock()
    return svc


def _bot(*, bot_type: str, active_engine=None, template_type=None) -> dict:
    return {
        "id": 1,
        "owner_id": OWNER,
        "bot_type": bot_type,
        "active_engine": active_engine,
        "template_type": template_type,
    }


def _add(service):
    # operator == owner -> OWNER 权限直接放行，不触 get_user_role
    return service.add_collaborator(
        bot_id="bot-123",
        owner_id=OWNER,
        user_id=MEMBER,
        operator_id=OWNER,
        user_name="张三",
        role=CollaboratorRole.ADMIN,
        env="dev",
    )


def test_add_collaborator_coding_app_allowed(service, bot_repo, collaborator_repo):
    """coding 应用（claude_code + applicationCoding，bot_type 非 service）-> 放行。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(
        bot_type="personal",
        active_engine="claude_code",
        template_type="applicationCoding",
    )

    record = _add(service)

    assert record is collaborator_repo.insert.return_value
    collaborator_repo.insert.assert_called_once()


def test_add_collaborator_member_management_flag_allowed(
    service, bot_repo, collaborator_repo, template_service
):
    """模板 advanced_config.member_management=true -> 非 service / 非 coding 也放行。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(
        bot_type="personal",
        active_engine="openclaw",
        template_type="chat",
    )
    template_service.get_template_config.return_value = {
        "bot_template_config": {"advanced_config": {"member_management": True}}
    }

    record = _add(service)

    assert record is collaborator_repo.insert.return_value
    collaborator_repo.insert.assert_called_once()


def test_add_collaborator_member_management_requires_boolean_true(service, bot_repo, template_service):
    """member_management 只有布尔 True 放行，字符串等 truthy 值不扩权。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(
        bot_type="personal",
        active_engine="openclaw",
        template_type="chat",
    )
    template_service.get_template_config.return_value = {
        "bot_template_config": {"advanced_config": {"member_management": "true"}}
    }

    with pytest.raises(BotNotServiceTypeError):
        _add(service)


def test_add_collaborator_service_type_still_allowed(service, bot_repo, collaborator_repo):
    """回归：service 类型仍按原逻辑放行。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(bot_type="service")

    record = _add(service)

    assert record is collaborator_repo.insert.return_value


def test_add_collaborator_non_service_non_coding_rejected(service, bot_repo):
    """非 service 且非 coding 应用 -> 抛 BotNotServiceTypeError。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(
        bot_type="personal",
        active_engine="claude_code",
        template_type="chat",  # template_type 不符
    )

    with pytest.raises(BotNotServiceTypeError):
        _add(service)


def test_add_collaborator_coding_requires_both_conditions(service, bot_repo):
    """仅 active_engine 命中、template_type 缺失 -> 不算 coding 应用，拒绝。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(
        bot_type="personal",
        active_engine="claude_code",
        template_type=None,
    )

    with pytest.raises(BotNotServiceTypeError):
        _add(service)


def test_add_collaborator_bot_not_found(service, bot_repo):
    """Bot 不存在 -> BotNotFoundError（前置分支）。"""
    bot_repo.get_by_id_and_owner.return_value = None

    with pytest.raises(BotNotFoundError):
        _add(service)


# ── on_collaboration_changed: passport-admin sync (required passport_plugin) ──
#
# passport_plugin / resolver_provider / device_fs_dispatcher_provider are now
# required. These exercise the unconditional passport-sync path (the old
# `if self._passport_plugin is not None` / `if resolver is None: return` guards
# are gone). The resolver thunk resolves to "no running device" so the
# credentials-sync leg is a clean no-op and we stay focused on the passport leg.

def _service_for_sync(passport):
    resolver = Mock()
    resolver.resolve_for_bot.side_effect = RuntimeError("no device in test")
    return CollaboratorService(
        collaborator_repo=Mock(),
        bot_repo=Mock(),
        passport_plugin=passport,
        resolver_provider=lambda: resolver,
        device_fs_dispatcher_provider=lambda: Mock(),
    )


def _admin_record(user_id):
    return CollaboratorRecord(
        id=1, bot_pk=1, bot_id="bot1", user_id=user_id,
        role=CollaboratorRole.ADMIN, owner_id="owner1", operator_id="op1", env="dev",
    )


def test_on_collaboration_changed_syncs_admins_to_passport():
    passport = Mock()
    svc = _service_for_sync(passport)
    svc._collaborator_repo.list_by_bot.return_value = [_admin_record("admin001")]
    svc._bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}

    svc.on_collaboration_changed("bot1", "owner1", env="dev")

    passport.update_passport.assert_called_once_with(
        bot_id="bot1", user_id="owner1", admins=["admin001"],
    )


def test_on_collaboration_changed_passport_failure_is_swallowed():
    passport = Mock()
    passport.update_passport.side_effect = RuntimeError("passport service down")
    svc = _service_for_sync(passport)
    svc._collaborator_repo.list_by_bot.return_value = []
    svc._bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}

    # A passport-sync failure must not propagate out of the collaboration hook.
    svc.on_collaboration_changed("bot1", "owner1", env="dev")

    passport.update_passport.assert_called_once()
