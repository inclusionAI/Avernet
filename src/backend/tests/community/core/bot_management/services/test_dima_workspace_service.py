"""Tests for WorkspaceHostingClient.query_staff_department and WorkspaceHostingService.create_workspace_for_bot.

Covers:
- WorkspaceHostingClient.query_staff_department: 正常查询、API 返回失败、网络异常、data 为字符串
- WorkspaceHostingClient.__init__: 环境区分选择 aixcore_base_url（prod vs pre/dev）
- WorkspaceHostingService.create_workspace_for_bot: 动态查询 department_id、查询失败兜底、
  已有 dima_space_id 跳过创建
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch, PropertyMock

from injector import Injector, InstanceProvider, singleton

import pytest

from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client import WorkspaceHostingClient
from agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_service import (
    WorkspaceHostingService,
    _DEFAULT_DEPARTMENT_ID,
    _FIXED_ADMIN_MEMBERS,
)
from agentclaw.community.di.config import WorkspaceHostingConfig


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_config(
    *,
    aixcore_base_url: str = "https://aixcore.teamclaw.com",
    aixcore_base_url_pre: str = "https://aixcore-pre.teamclaw.com",
) -> WorkspaceHostingConfig:
    """Build a WorkspaceHostingConfig with sensible test defaults."""
    return WorkspaceHostingConfig(
        base_url="https://devapi.teamclaw.com",
        access_key="test-ak-12345",
        access_secret="0123456789ABCDEF",  # 16 chars for AES
        tenant="test",
        timeout=10,
        aixcore_base_url=aixcore_base_url,
        aixcore_base_url_pre=aixcore_base_url_pre,
    )


def _make_dima_client(config: WorkspaceHostingConfig | None = None) -> WorkspaceHostingClient:
    """Build a WorkspaceHostingClient through its real DI constructor.

    Wires ``WorkspaceHostingConfig`` into an ``Injector`` so the client's
    ``@inject __init__`` runs normally (no ``object.__new__``). After
    construction the real ``requests.Session`` is swapped for a ``MagicMock``
    purely as offline isolation for client-side unit tests (this does not
    bypass DI — the constructor already executed via the injector).
    """
    cfg = config or _make_config()
    injector = Injector()
    injector.binder.bind(WorkspaceHostingConfig, InstanceProvider(cfg), scope=singleton)
    injector.binder.bind(WorkspaceHostingClient, to=WorkspaceHostingClient, scope=singleton)
    client = injector.get(WorkspaceHostingClient)
    client.session = MagicMock()
    return client


def _make_workspace_service(client: WorkspaceHostingClient | None = None) -> WorkspaceHostingService:
    """Build a WorkspaceHostingService through its real DI constructor.

    The ``@inject __init__`` is driven by an ``Injector`` so the service is
    wired to ``client`` via proper DI instead of ``object.__new__``.
    """
    hosting_client = client or _make_dima_client()
    injector = Injector()
    injector.binder.bind(WorkspaceHostingClient, InstanceProvider(hosting_client), scope=singleton)
    injector.binder.bind(WorkspaceHostingService, to=WorkspaceHostingService, scope=singleton)
    return injector.get(WorkspaceHostingService)

# ── WorkspaceHostingClient.__init__ 环境区分 ────────────────────────────────────────


class TestWorkspaceHostingClientInitEnvRouting:
    """WorkspaceHostingClient.__init__ 根据环境选择 aixcore_base_url。"""

    @patch("agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client.get_current_env", return_value="prod")
    def test_prod_env_uses_aixcore_base_url(self, mock_env):
        cfg = _make_config(
            aixcore_base_url="https://aixcore.teamclaw.com",
            aixcore_base_url_pre="https://aixcore-pre.teamclaw.com",
        )
        client = WorkspaceHostingClient(cfg)
        assert client.aixcore_base_url == "https://aixcore.teamclaw.com"

    @patch("agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client.get_current_env", return_value="pre")
    def test_pre_env_uses_aixcore_base_url_pre(self, mock_env):
        cfg = _make_config(
            aixcore_base_url="https://aixcore.teamclaw.com",
            aixcore_base_url_pre="https://aixcore-pre.teamclaw.com",
        )
        client = WorkspaceHostingClient(cfg)
        assert client.aixcore_base_url == "https://aixcore-pre.teamclaw.com"

    @patch("agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client.get_current_env", return_value="dev")
    def test_dev_env_uses_aixcore_base_url_pre(self, mock_env):
        cfg = _make_config(
            aixcore_base_url="https://aixcore.teamclaw.com",
            aixcore_base_url_pre="https://aixcore-pre.teamclaw.com",
        )
        client = WorkspaceHostingClient(cfg)
        assert client.aixcore_base_url == "https://aixcore-pre.teamclaw.com"

    @patch("agentclaw.community.core.bot_management.services.aicoding.workspace_hosting_client.get_current_env", return_value="prod")
    def test_strips_trailing_slash(self, mock_env):
        cfg = _make_config(
            aixcore_base_url="https://aixcore.teamclaw.com/",
            aixcore_base_url_pre="https://aixcore-pre.teamclaw.com/",
        )
        client = WorkspaceHostingClient(cfg)
        assert not client.aixcore_base_url.endswith("/")


# ── WorkspaceHostingClient.query_staff_department ────────────────────────────────────


class TestQueryStaffDepartment:
    """WorkspaceHostingClient.query_staff_department 查询员工部门。"""

    def test_success_returns_dept_no(self):
        client = _make_dima_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {
                "deptNo": "F4858",
                "deptName": "蚂蚁集团-大安全-大安全技术部",
                "name": "张三",
            },
            "errorCode": 0,
            "errorMsg": "",
        }
        client.session.request.return_value = mock_response

        result = client.query_staff_department("100000")

        assert result == "F4858"
        client.session.request.assert_called_once()
        call_kwargs = client.session.request.call_args[1]
        assert call_kwargs["method"] == "POST"
        assert "/api/staffInfo/queryEmpInfo" in call_kwargs["url"]
        assert call_kwargs["json"] == {"workNo": "100000"}

    def test_api_returns_failure_returns_none(self):
        client = _make_dima_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": False,
            "errorCode": 404,
            "errorMsg": "User not found",
        }
        client.session.request.return_value = mock_response

        result = client.query_staff_department("999999")

        assert result is None

    def test_network_exception_returns_none(self):
        client = _make_dima_client()
        client.session.request.side_effect = Exception("Connection refused")

        result = client.query_staff_department("100000")

        assert result is None

    def test_data_is_json_string_parses_correctly(self):
        """API 返回的 data 字段是 JSON 字符串而非 dict 时也能正确解析。"""
        client = _make_dima_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": json.dumps({"deptNo": "D1234", "name": "李四"}),
        }
        client.session.request.return_value = mock_response

        result = client.query_staff_department("123456")

        assert result == "D1234"

    def test_missing_dept_no_returns_none(self):
        """API 返回成功但 data 中没有 deptNo 字段。"""
        client = _make_dima_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {"name": "王五", "deptName": "某部门"},
        }
        client.session.request.return_value = mock_response

        result = client.query_staff_department("123456")

        assert result is None

    def test_empty_data_returns_none(self):
        """API 返回成功但 data 为空 dict。"""
        client = _make_dima_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {},
        }
        client.session.request.return_value = mock_response

        result = client.query_staff_department("123456")

        assert result is None

    def test_uses_aixcore_base_url(self):
        """确认请求 URL 使用 aixcore_base_url 而非 base_url。"""
        client = _make_dima_client()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "data": {"deptNo": "F4858"},
        }
        client.session.request.return_value = mock_response
        client.aixcore_base_url = "https://custom-aixcore.example.com"

        client.query_staff_department("100000")

        call_kwargs = client.session.request.call_args[1]
        assert call_kwargs["url"] == "https://custom-aixcore.example.com/api/staffInfo/queryEmpInfo"


# ── WorkspaceHostingService.create_workspace_for_bot ────────────────────────


class TestCreateWorkspaceForBot:
    """WorkspaceHostingService.create_workspace_for_bot 动态查询 department_id。"""

    def test_queries_department_and_creates_workspace(self):
        """正常流程：查询部门 → 创建空间 → 返回 workspace_id。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(return_value={
            "success": True,
            "data": {"workspaceId": "WS001"},
        })
        svc = _make_workspace_service(client)

        template_config = {}
        result = svc.create_workspace_for_bot(
            staff_id="100000",
            bot_id="bot_001",
            bot_name="TestBot",
            template_config=template_config,
        )

        assert result == "WS001"
        assert template_config["dima_space_id"] == "WS001"
        client.query_staff_department.assert_called_once_with("100000")
        client.create_workspace.assert_called_once_with(
            staff_id="100000",
            workspace_name="TestBot_bot_001",
            department_id="D9999",
            workspace_desc="Workspace for bot bot_001",
        )

    def test_falls_back_to_default_when_query_fails(self):
        """查询部门失败时，使用默认兜底值 _DEFAULT_DEPARTMENT_ID。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value=None)
        client.create_workspace = MagicMock(return_value={
            "success": True,
            "data": {"workspaceId": "WS002"},
        })
        svc = _make_workspace_service(client)

        template_config = {}
        result = svc.create_workspace_for_bot(
            staff_id="100000",
            bot_id="bot_002",
            bot_name="TestBot",
            template_config=template_config,
        )

        assert result == "WS002"
        client.create_workspace.assert_called_once()
        call_kwargs = client.create_workspace.call_args[1]
        assert call_kwargs["department_id"] == _DEFAULT_DEPARTMENT_ID

    def test_falls_back_to_default_when_query_returns_empty_string(self):
        """查询部门返回空字符串时，使用默认兜底值。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="")
        client.create_workspace = MagicMock(return_value={
            "success": True,
            "data": {"workspaceId": "WS003"},
        })
        svc = _make_workspace_service(client)

        template_config = {}
        result = svc.create_workspace_for_bot(
            staff_id="100000",
            bot_id="bot_003",
            bot_name="TestBot",
            template_config=template_config,
        )

        assert result == "WS003"
        call_kwargs = client.create_workspace.call_args[1]
        assert call_kwargs["department_id"] == _DEFAULT_DEPARTMENT_ID

    def test_skips_creation_when_dima_space_id_exists(self):
        """template_config 中已有 dima_space_id 时，跳过创建。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock()
        client.create_workspace = MagicMock()
        svc = _make_workspace_service(client)

        template_config = {"dima_space_id": "EXISTING_WS"}
        result = svc.create_workspace_for_bot(
            staff_id="100000",
            bot_id="bot_004",
            bot_name="TestBot",
            template_config=template_config,
        )

        assert result == "EXISTING_WS"
        client.query_staff_department.assert_not_called()
        client.create_workspace.assert_not_called()

    def test_returns_none_when_create_workspace_raises(self):
        """create_workspace 抛异常时返回 None。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(side_effect=Exception("API error"))
        svc = _make_workspace_service(client)

        result = svc.create_workspace_for_bot(
            staff_id="100000",
            bot_id="bot_005",
            bot_name="TestBot",
        )

        assert result is None

    def test_no_template_config_does_not_crash(self):
        """不传 template_config 时不会崩溃。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(return_value={
            "success": True,
            "data": {"workspaceId": "WS006"},
        })
        svc = _make_workspace_service(client)

        result = svc.create_workspace_for_bot(
            staff_id="100000",
            bot_id="bot_006",
            bot_name="TestBot",
        )

        assert result == "WS006"

    def test_default_department_id_is_52146(self):
        """确认兜底默认部门 ID 为 52146。"""
        assert _DEFAULT_DEPARTMENT_ID == "52146"

    def test_workspace_name_format(self):
        """确认 workspace_name 格式为 {bot_name}_{bot_id}。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(return_value={
            "success": True,
            "data": {"workspaceId": "WS007"},
        })
        svc = _make_workspace_service(client)

        svc.create_workspace_for_bot(
            staff_id="100000",
            bot_id="20260601_abc123",
            bot_name="我的Bot",
        )

        call_kwargs = client.create_workspace.call_args[1]
        assert call_kwargs["workspace_name"] == "我的Bot_20260601_abc123"

    def test_swallows_exception_by_default(self):
        """默认 raise_on_failure=False：DIMA 报错 → 返回 None，不抛。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(side_effect=Exception(
            "DIMA API error [ARK_RS_530013001]: 空间名称已经被占用"
        ))
        svc = _make_workspace_service(client)

        result = svc.create_workspace_for_bot(
            staff_id="100000", bot_id="bot_dup", bot_name="DupBot",
        )

        assert result is None

    def test_raises_when_raise_on_failure_true(self):
        """raise_on_failure=True：DIMA 原异常透传，调用方可拿到错误码与消息。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(side_effect=Exception(
            "DIMA API error [ARK_RS_530013001]: 空间名称【DupBot_bot_dup】已经被占用"
        ))
        svc = _make_workspace_service(client)

        with pytest.raises(Exception) as exc_info:
            svc.create_workspace_for_bot(
                staff_id="100000",
                bot_id="bot_dup",
                bot_name="DupBot",
                raise_on_failure=True,
            )

        assert "ARK_RS_530013001" in str(exc_info.value)
        assert "已经被占用" in str(exc_info.value)



# ── addMembers（临时本地改动）：新增管理员调用 ────────────────────────────


class TestAddAdminMembersClient:
    """WorkspaceHostingClient.add_admin_members：透传 addMembers 接口。"""

    def test_add_admin_members_builds_admin_payload(self):
        """verify body: targetType=WORKSPACE / targetId / roleId=ADMIN / memberStaffIds。
        走 _make_request(POST)，固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定固定"""
        client = _make_dima_client()
        client._make_request = MagicMock(return_value={"success": True})

        result = client.add_admin_members(
            staff_id="100000",
            workspace_id="WS001",
            member_staff_ids=["382716", "040981"],
        )

        assert result == {"success": True}
        client._make_request.assert_called_once()
        args, kwargs = client._make_request.call_args
        assert args[0] == "POST"
        assert args[1] == "/arkcooprod/openapi/role/member/addMembers"
        assert args[2] == "100000"  # staff_id
        assert kwargs["params"] == {"staffId": "100000"}
        assert kwargs["data"] == {
            "targetType": "WORKSPACE",
            "targetId": "WS001",
            "roleId": "ADMIN",
            "memberStaffIds": ["382716", "040981"],
        }
        assert kwargs["allow_empty_data"] is True

    def test_add_admin_members_returns_make_request_result(self):
        """透传 _make_request 返回值，不额外解析。"""
        client = _make_dima_client()
        client._make_request = MagicMock(return_value={"success": True, "code": "ARK_RS_100000200"})

        result = client.add_admin_members(
            staff_id="326018", workspace_id="W23001000283", member_staff_ids=["382716"],
        )

        assert result == {"success": True, "code": "ARK_RS_100000200"}

    def test_add_admin_members_propagates_make_request_error(self):
        """_make_request 抛异常时透传（由 service 层吞/抛）。"""
        client = _make_dima_client()
        client._make_request = MagicMock(side_effect=Exception("DIMA API error [ARK_RS_310011405]"))

        with pytest.raises(Exception) as exc_info:
            client.add_admin_members(
                staff_id="100000", workspace_id="WS001", member_staff_ids=["382716"],
            )

        assert "ARK_RS_310011405" in str(exc_info.value)


# ── create_workspace_for_bot：新增 add_admin_members 联动分支 ────────────────


class TestCreateWorkspaceThenAddAdmins:
    """workspace 创建成功后自动加固定管理员（临时本地改动）。"""

    def test_calls_add_admin_members_with_fixed_members_after_creation(self):
        """创建成功 → 用固定 9 工号调 add_admin_members，并返回 workspace_id。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(return_value={
            "success": True, "data": {"workspaceId": "WS001"},
        })
        client.add_admin_members = MagicMock(return_value={"success": True})
        svc = _make_workspace_service(client)

        template_config = {}
        result = svc.create_workspace_for_bot(
            staff_id="100000", bot_id="bot_001", bot_name="TestBot",
            template_config=template_config,
        )

        assert result == "WS001"
        assert template_config["dima_space_id"] == "WS001"
        client.add_admin_members.assert_called_once_with(
            staff_id="100000",
            workspace_id="WS001",
            member_staff_ids=list(_FIXED_ADMIN_MEMBERS),
        )

    def test_fixed_admin_members_list(self):
        """确认固定管理员列表内容与数量（与 service 常量保持一致）。"""
        assert _FIXED_ADMIN_MEMBERS == [
            "382716", "136677", "204696", "040981", "151710",
            "024021", "137454", "150839", "227210", "246667", "511549",
        ]
        assert len(_FIXED_ADMIN_MEMBERS) == 11

    def test_add_admin_members_failure_does_not_block_bot_creation(self):
        """add_admin_members 失败（ADMIN 无权场景 ARK_RS_310011405）只记 warning，
        仍正常返回 workspace_id（不加管理员不应让创建 bot 失败）。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(return_value={
            "success": True, "data": {"workspaceId": "WS001"},
        })
        client.add_admin_members = MagicMock(side_effect=Exception(
            "DIMA API error [ARK_RS_310011405]: 本操作【添加管理员】需要【空间管理员】方可进行"
        ))
        svc = _make_workspace_service(client)

        template_config = {}
        result = svc.create_workspace_for_bot(
            staff_id="100000", bot_id="bot_001", bot_name="TestBot",
            template_config=template_config,
        )

        # workspace 创建成功，dima_space_id 已落，加管理员失败不影响结果
        assert result == "WS001"
        assert template_config["dima_space_id"] == "WS001"
        client.add_admin_members.assert_called_once()

    def test_skips_add_admins_when_dima_space_id_already_exists(self):
        """已有 dima_space_id 跳过创建时，也不调 add_admin_members。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock()
        client.create_workspace = MagicMock()
        client.add_admin_members = MagicMock()
        svc = _make_workspace_service(client)

        result = svc.create_workspace_for_bot(
            staff_id="100000", bot_id="bot_004", bot_name="TestBot",
            template_config={"dima_space_id": "EXISTING_WS"},
        )

        assert result == "EXISTING_WS"
        client.add_admin_members.assert_not_called()

    def test_skips_add_admins_when_no_workspace_id(self):
        """create_workspace 未返回 workspace_id 时不调 add_admin_members。"""
        client = _make_dima_client()
        client.query_staff_department = MagicMock(return_value="D9999")
        client.create_workspace = MagicMock(return_value={"success": True, "data": {}})
        client.add_admin_members = MagicMock()
        svc = _make_workspace_service(client)

        svc.create_workspace_for_bot(
            staff_id="100000", bot_id="bot_005", bot_name="TestBot",
        )

        client.add_admin_members.assert_not_called()
