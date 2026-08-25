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
    PermissionLevel,
)
from agentclaw.community.core.bot_collaborator.services.aicoding.member_management_capability import (
    AICodingMemberManagementCapability,
)
from agentclaw.community.core.bot_collaborator.services.member_management_capability import (
    MemberManagementCapabilityService,
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
        credentials_admins_writer=Mock(),
        space_access_service=Mock(),
        member_management_capability_service=MemberManagementCapabilityService(
            engine_capabilities=(AICodingMemberManagementCapability(template_service),),
        ),
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


def test_add_collaborator_personal_coding_claude_code_allowed(service, bot_repo, collaborator_repo):
    """个人 Coding Bot（claude_code + personalCoding，bot_type 非 service）-> 放行。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(
        bot_type="personal",
        active_engine="claude_code",
        template_type="personalCoding",
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


def test_add_collaborator_member_management_requires_boolean_true(
    service, bot_repo, template_service
):
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


def test_add_collaborator_service_type_still_allowed(
    service, bot_repo, collaborator_repo
):
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
    return CollaboratorService(
        collaborator_repo=Mock(),
        bot_repo=Mock(),
        passport_plugin=passport,
        credentials_admins_writer=Mock(),
        space_access_service=Mock(),
    )


def _admin_record(user_id):
    return CollaboratorRecord(
        id=1,
        bot_pk=1,
        bot_id="bot1",
        user_id=user_id,
        role=CollaboratorRole.ADMIN,
        owner_id="owner1",
        operator_id="op1",
        env="dev",
    )


def test_on_collaboration_changed_syncs_admins_to_passport():
    passport = Mock()
    svc = _service_for_sync(passport)
    svc._collaborator_repo.list_by_bot.return_value = [_admin_record("admin001")]
    svc._bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot1",
        "owner_id": "owner1",
    }

    svc.on_collaboration_changed("bot1", "owner1", env="dev")

    passport.update_passport.assert_called_once_with(
        bot_id="bot1",
        user_id="owner1",
        admins=["admin001"],
    )


def test_on_collaboration_changed_passport_failure_is_swallowed():
    passport = Mock()
    passport.update_passport.side_effect = RuntimeError("passport service down")
    svc = _service_for_sync(passport)
    svc._collaborator_repo.list_by_bot.return_value = []
    svc._bot_repo.get_by_id_and_owner.return_value = {
        "bot_id": "bot1",
        "owner_id": "owner1",
    }

    # A passport-sync failure must not propagate out of the collaboration hook.
    svc.on_collaboration_changed("bot1", "owner1", env="dev")

    passport.update_passport.assert_called_once()


# ── on_collaboration_changed: delegates the .credentials admins write to the
#    DeviceCredentialsAdminsWriter (the teclaw service-bot admins-sync fix) ──


class _RecordingWriter:
    """Records sync_on_change calls — stands in for the credentials writer."""

    def __init__(self) -> None:
        self.sync_calls: list[tuple[str, str, list[str]]] = []

    def sync_on_change(self, bot_id: str, owner_id: str, admins: list[str]) -> None:
        self.sync_calls.append((bot_id, owner_id, list(admins)))


def test_on_collaboration_changed_delegates_admins_to_credentials_writer():
    writer = _RecordingWriter()
    collaborator_repo = Mock()
    collaborator_repo.list_by_bot.return_value = [
        _admin_record("admin001"),
        _admin_record("admin002"),
    ]
    bot_repo = Mock()
    bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "owner_id": "owner1"}
    svc = CollaboratorService(
        collaborator_repo=collaborator_repo,
        bot_repo=bot_repo,
        passport_plugin=Mock(),
        credentials_admins_writer=writer,
        space_access_service=Mock(),
    )

    svc.on_collaboration_changed("bot1", "owner1", env="dev")

    # admins (role=admin) computed from the collaborator list are handed to the writer
    assert writer.sync_calls == [("bot1", "owner1", ["admin001", "admin002"])]


def _collaborator_record(user_id: str = MEMBER) -> CollaboratorRecord:
    return CollaboratorRecord(
        id=42,
        bot_pk=1,
        bot_id="bot-123",
        owner_id=OWNER,
        user_id=user_id,
        role=CollaboratorRole.MEMBER,
        operator_id=OWNER,
        env="dev",
    )


def test_leave_collaboration_allows_member_self_exit(
    service, bot_repo, collaborator_repo
):
    """成员主动退出协作：只删除当前用户自己的协作者记录，不要求 admin 权限。"""
    bot_repo.get_by_id_and_owner.return_value = _bot(bot_type="service")
    collaborator_repo.get_by_bot_and_user.return_value = _collaborator_record()
    collaborator_repo.delete.return_value = True

    result = service.leave_collaboration(
        bot_id="bot-123",
        owner_id=OWNER,
        user_id=MEMBER,
        env="dev",
    )

    assert result is True
    collaborator_repo.get_by_bot_and_user.assert_called_once_with(1, MEMBER, "dev")
    collaborator_repo.delete.assert_called_once_with(42)
    service.on_collaboration_changed.assert_called_once_with("bot-123", OWNER, "dev")


def test_leave_collaboration_rejects_non_collaborator(
    service, bot_repo, collaborator_repo
):
    """非协作者没有自己的记录，退出返回 CollaboratorNotFoundError。"""
    from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
        CollaboratorNotFoundError,
    )

    bot_repo.get_by_id_and_owner.return_value = _bot(bot_type="service")
    collaborator_repo.get_by_bot_and_user.return_value = None

    with pytest.raises(CollaboratorNotFoundError):
        service.leave_collaboration(
            bot_id="bot-123",
            owner_id=OWNER,
            user_id="stranger",
            env="dev",
        )

    collaborator_repo.delete.assert_not_called()
    service.on_collaboration_changed.assert_not_called()


def test_leave_collaboration_owner_is_not_collaborator_record(
    service, bot_repo, collaborator_repo
):
    """Owner 不是协作者记录，不能使用成员退出接口。"""
    from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
        CollaboratorNotFoundError,
    )

    bot_repo.get_by_id_and_owner.return_value = _bot(bot_type="service")

    with pytest.raises(CollaboratorNotFoundError):
        service.leave_collaboration(
            bot_id="bot-123",
            owner_id=OWNER,
            user_id=OWNER,
            env="dev",
        )

    collaborator_repo.get_by_bot_and_user.assert_not_called()
    collaborator_repo.delete.assert_not_called()



def test_batch_list_collaborators_accepts_multiple_bot_ids(
    service, bot_repo, collaborator_repo
):
    """Batch list resolves each Bot owner and returns one flat collaborator list."""
    bot_repo.get_by_id.side_effect = lambda bot_id: {
        "bot-a": {"id": 11, "owner_id": "owner-a"},
        "bot-b": {"id": 22, "owner_id": "owner-b"},
    }.get(bot_id)
    record_a = Mock(bot_id="bot-a")
    record_b = Mock(bot_id="bot-b")
    collaborator_repo.list_by_bot.side_effect = [[record_a], [record_b]]
    service.check_permission = Mock()

    records = service.batch_list_collaborators(
        bot_ids=["bot-a", "bot-b", "bot-a"],
        user_id="user-1",
        role="admin",
        env="dev",
    )

    assert records == [record_a, record_b]
    assert bot_repo.get_by_id.call_count == 2
    service.check_permission.assert_any_call(
        bot_pk=11,
        user_id="user-1",
        owner_id="owner-a",
        required_level=PermissionLevel.MEMBER,
        env="dev",
    )
    assert collaborator_repo.list_by_bot.call_count == 2


def test_batch_list_collaborators_skips_missing_candidate(
    service, bot_repo, collaborator_repo
):
    """Batch mode omits missing Bots instead of failing the whole candidate set."""
    bot_repo.get_by_id.side_effect = lambda bot_id: (
        {"id": 11, "owner_id": "owner-a"} if bot_id == "bot-a" else None
    )
    record = Mock(bot_id="bot-a")
    collaborator_repo.list_by_bot.return_value = [record]
    service.check_permission = Mock()

    records = service.batch_list_collaborators(
        bot_ids=["bot-a", "bot-missing"],
        user_id="user-1",
        env="dev",
    )

    assert records == [record]


def test_list_collaborators_single_keeps_strict_not_found(service, bot_repo):
    """The legacy single-Bot request keeps its existing strict error semantics."""
    bot_repo.get_by_id_and_owner.return_value = None

    with pytest.raises(BotNotFoundError):
        service.list_collaborators(
            bot_id="bot-missing",
            owner_id=OWNER,
            user_id="user-1",
            env="dev",
        )


def test_list_collaborators_single_with_owner_keeps_legacy_behavior(
    service, bot_repo, collaborator_repo
):
    """Legacy bot_id + owner_id calls keep the original repository and result path."""
    bot = {"id": 11, "owner_id": OWNER}
    record = Mock(bot_id="bot-123", owner_id=OWNER)
    bot_repo.get_by_id_and_owner.return_value = bot
    collaborator_repo.list_by_bot.return_value = [record]
    service.check_permission = Mock()

    records = service.list_collaborators(
        bot_id="bot-123",
        owner_id=OWNER,
        user_id=MEMBER,
        role="admin",
        env="dev",
    )

    assert records == [record]
    bot_repo.get_by_id_and_owner.assert_called_once_with("bot-123", OWNER)
    bot_repo.get_by_id.assert_not_called()
    service.check_permission.assert_called_once_with(
        bot_pk=11,
        user_id=MEMBER,
        owner_id=OWNER,
        required_level=PermissionLevel.MEMBER,
        env="dev",
    )
    collaborator_repo.list_by_bot.assert_called_once_with(
        bot_id="bot-123",
        owner_id=OWNER,
        env="dev",
        role="admin",
    )


def test_batch_list_collaborators_skips_permission_denied_candidate(
    service, bot_repo, collaborator_repo
):
    """One inaccessible Bot must not fail a batch containing an accessible Bot."""
    bot_repo.get_by_id.side_effect = lambda bot_id: {
        "bot-a": {"id": 11, "owner_id": "owner-a"},
        "bot-b": {"id": 22, "owner_id": "owner-b"},
    }[bot_id]
    record = Mock(bot_id="bot-a")
    collaborator_repo.list_by_bot.return_value = [record]

    def check_permission(**kwargs):
        if kwargs["bot_pk"] == 22:
            from agentclaw.community.core.bot_collaborator.services.collaborator_service import (
                PermissionDeniedError,
            )
            raise PermissionDeniedError("no access")

    service.check_permission = Mock(side_effect=check_permission)

    records = service.batch_list_collaborators(
        bot_ids=["bot-a", "bot-b"],
        user_id=MEMBER,
        env="dev",
    )

    assert records == [record]
    collaborator_repo.list_by_bot.assert_called_once_with(
        bot_id="bot-a",
        owner_id="owner-a",
        env="dev",
        role=None,
    )
