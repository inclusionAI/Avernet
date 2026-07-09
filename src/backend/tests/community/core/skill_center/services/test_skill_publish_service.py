"""Tests for SkillPublishService — 状态机 + 传播 + 打包上传。

覆盖：
  - 状态机合法/非法转移 (S1-S9)
  - 升级发布 (U1-U5)
  - 卷瓜传播时机与容错 (P1-P8)
  - 打包上传 (O1-O4)
  - SC_STATUS_MAP 映射 (M1-M8)
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentclaw.community.core.skill_center.services.skill_publish_service import (
    InvalidTransitionError,
    SC_STATUS_MAP,
    SkillPublishService,
    VALID_TRANSITIONS,
)
from agentclaw.community.plugins.local.oss_storage import MockObjectStoragePlugin


# ── helpers ──────────────────────────────────────────────────────────


def _make_skill(**overrides) -> dict:
    """构造一个 skill dict，可按需覆盖字段。"""
    base = {
        "id": "1",
        "name": "test-skill",
        "description": "desc",
        "status": "DEVELOPING",
        "version": 1,
        "skill_uuid": "uuid-abc",
        "link_name": "test-skill",
        "git_path": "",
        "source_type": "git",
        "category": "general",
        "tags": "[]",
        "input_schema": None,
        "output_schema": None,
        "is_public": False,
        "is_builtin": False,
        "user_id": "user1",
        "risk_tags": None,
        "mcp_dependencies": None,
        "bolt_id": "default",
    }
    base.update(overrides)
    return base


def _make_service(
    skill=None,
    sc_upload_result=None,
    sc_status_result=None,
    sc_versions_result=None,
    with_propagation=True,
    create_returns=None,
    all_skills=None,
):
    """构造 SkillPublishService，所有依赖全 mock。

    OSS is always supplied via ``MockObjectStoragePlugin`` — the service's
    ctor now requires a non-``None`` ``ObjectStoragePlugin``. Tests that
    care about OSS interactions read calls off the returned mock; tests
    that don't care just leave it alone (the default mock silently
    succeeds for ``put_object`` / ``ensure_directory`` and returns a
    ``mock://...`` URL from ``sign_url``).
    """
    repo = MagicMock()
    repo.get_by_id.return_value = skill
    repo.update.return_value = skill
    repo.create.return_value = create_returns or (
        {**skill, "id": "new-1", "status": "DEVELOPING"} if skill else None
    )
    repo.list_skills.return_value = all_skills or []

    sc_client = MagicMock()
    sc_client.upload_and_publish.return_value = sc_upload_result or {
        "success": True, "data": {"skillCode": "test"},
    }
    sc_client.query_publish_status.return_value = sc_status_result or {
        "success": True, "data": {"status": "PRE_RELEASING", "isCompleted": False},
    }
    sc_client.list_versions.return_value = sc_versions_result or []

    # ``with_propagation=False`` bypasses the type contract (the service
    # ctor now declares ``propagation_service: SkillPropagationService``
    # without an Optional) to exercise the defensive ``if not
    # self._propagation`` guard kept inside ``propagate_*`` for safety.
    # The DI graph always supplies a real propagation service; only this
    # direct unit-test path passes ``None``.
    propagation = MagicMock() if with_propagation else None
    oss = MockObjectStoragePlugin()
    # Pin sign_url to the legacy test sentinel so existing assertions
    # ("assert result == 'https://oss.example.com/signed'") keep working.
    oss.sign_url.side_effect = None
    oss.sign_url.return_value = "https://oss.example.com/signed"

    svc = SkillPublishService(
        skill_repo=repo,
        skill_center_client=sc_client,
        oss_client=oss,
        propagation_service=propagation,
    )
    return svc, repo, sc_client, propagation, oss


# ── 状态机定义验证 ───────────────────────────────────────────────────


class TestValidTransitions:
    def test_developing_can_go_pending(self):
        assert "PENDING" in VALID_TRANSITIONS["DEVELOPING"]

    def test_pending_can_go_published_or_rejected(self):
        assert VALID_TRANSITIONS["PENDING"] == {"PUBLISHED", "REJECTED"}

    def test_published_can_only_go_offline(self):
        assert VALID_TRANSITIONS["PUBLISHED"] == {"OFFLINE"}

    def test_rejected_can_retry_pending(self):
        assert "PENDING" in VALID_TRANSITIONS["REJECTED"]

    def test_offline_is_terminal(self):
        assert "OFFLINE" not in VALID_TRANSITIONS


class TestScStatusMap:
    """M1-M8: 远程状态到本地状态映射。"""

    @pytest.mark.parametrize("remote,expected", [
        ("PRE_RELEASING", None),
        ("SECURITY_SCANNING", None),
        ("SECURITY_CHECK_PASSED", None),
        ("STANDARD_CHECK_PASSED", None),
        ("PUBLISHED", "PUBLISHED"),
        ("SECURITY_CHECK_FAILED", "REJECTED"),
        ("STANDARD_CHECK_FAILED", "REJECTED"),
    ])
    def test_known_status_mapping(self, remote, expected):
        assert SC_STATUS_MAP[remote] == expected

    def test_unknown_status_returns_none(self):
        assert SC_STATUS_MAP.get("SOME_UNKNOWN_STATUS") is None


# ── publish() ────────────────────────────────────────────────────────


class TestPublish:
    def test_developing_to_pending(self):
        """S1: DEVELOPING → PENDING"""
        skill = _make_skill(status="DEVELOPING")
        svc, repo, sc_client, _, _ = _make_service(skill=skill)

        result = svc.publish("1")

        assert result["success"] is True
        repo.update.assert_called_once_with("1", {"status": "PENDING"})
        sc_client.upload_and_publish.assert_called_once()

    def test_rejected_to_pending(self):
        """S2: REJECTED → PENDING (重试发布)"""
        skill = _make_skill(status="REJECTED")
        svc, repo, _, _, _ = _make_service(skill=skill)

        result = svc.publish("1")

        assert result["success"] is True
        repo.update.assert_called_once_with("1", {"status": "PENDING"})

    def test_published_cannot_publish(self):
        """S3: PUBLISHED → PENDING 非法"""
        skill = _make_skill(status="PUBLISHED")
        svc, _, _, _, _ = _make_service(skill=skill)

        with pytest.raises(InvalidTransitionError):
            svc.publish("1")

    def test_offline_cannot_publish(self):
        """S4: OFFLINE → 任何状态非法"""
        skill = _make_skill(status="OFFLINE")
        svc, _, _, _, _ = _make_service(skill=skill)

        with pytest.raises(InvalidTransitionError):
            svc.publish("1")

    def test_none_status_defaults_to_developing(self):
        """S9: status=None 按 DEVELOPING 处理"""
        skill = _make_skill(status=None)
        svc, repo, _, _, _ = _make_service(skill=skill)

        result = svc.publish("1")

        assert result["success"] is True

    def test_skill_not_found_raises(self):
        """skill 不存在抛 ValueError"""
        svc, _, _, _, _ = _make_service(skill=None)

        with pytest.raises(ValueError, match="not found"):
            svc.publish("999")

    def test_sc_upload_fail_returns_error(self):
        """SkillCenter 返回失败"""
        skill = _make_skill(status="DEVELOPING")
        svc, repo, _, _, _ = _make_service(
            skill=skill,
            sc_upload_result={"success": False, "error": "server error"},
        )

        result = svc.publish("1")

        assert result["success"] is False
        repo.update.assert_not_called()

    def test_publish_uses_name_as_code(self):
        """优先用 name 作为 skillCode"""
        skill = _make_skill(name="my-skill-name", skill_uuid="my-uuid")
        svc, _, sc_client, _, _ = _make_service(skill=skill)

        svc.publish("1")

        payload = sc_client.upload_and_publish.call_args[0][0]
        assert payload["skillCode"] == "my-skill-name"

    def test_publish_skill_code_uses_name(self):
        """skillCode 始终取 name"""
        skill = _make_skill(name="", skill_uuid="fallback-uuid")
        svc, _, sc_client, _, _ = _make_service(skill=skill)

        svc.publish("1")

        payload = sc_client.upload_and_publish.call_args[0][0]
        assert payload["skillCode"] == ""


# ── query_status() ───────────────────────────────────────────────────


class TestQueryStatus:
    def test_non_pending_returns_error(self):
        """S8: 非 PENDING 状态不允许轮询"""
        skill = _make_skill(status="DEVELOPING")
        svc, _, _, _, _ = _make_service(skill=skill)

        result = svc.query_status("1")

        assert result["success"] is False
        assert "仅 PENDING" in result["message"]

    def test_pending_continues_polling(self):
        """S7: 远程返回 PRE_RELEASING → 继续轮询"""
        skill = _make_skill(status="PENDING")
        svc, repo, _, _, _ = _make_service(
            skill=skill,
            sc_status_result={
                "success": True,
                "data": {"status": "PRE_RELEASING", "isCompleted": False},
            },
        )

        result = svc.query_status("1")

        assert result["success"] is True
        assert result["data"]["local_status"] == "PENDING"
        repo.update.assert_not_called()

    def test_pending_to_published(self):
        """S5: 远程返回 PUBLISHED → 更新为 PUBLISHED"""
        skill = _make_skill(status="PENDING", version=1)
        svc, repo, _, _, _ = _make_service(
            skill=skill,
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        result = svc.query_status("1")

        assert result["success"] is True
        assert result["data"]["local_status"] == "PUBLISHED"
        repo.update.assert_called_once_with("1", {"status": "PUBLISHED", "git_path": "center://uuid-abc"})

    def test_pending_to_rejected(self):
        """S6: 远程返回 SECURITY_CHECK_FAILED → REJECTED"""
        skill = _make_skill(status="PENDING")
        svc, repo, _, _, _ = _make_service(
            skill=skill,
            sc_status_result={
                "success": True,
                "data": {"status": "SECURITY_CHECK_FAILED", "isCompleted": True, "isSuccess": False},
            },
        )

        result = svc.query_status("1")

        assert result["data"]["local_status"] == "REJECTED"
        repo.update.assert_called_once_with("1", {"status": "REJECTED"})

    def test_sc_query_fail_returns_error(self):
        """SkillCenter 查询失败"""
        skill = _make_skill(status="PENDING")
        svc, _, _, _, _ = _make_service(
            skill=skill,
            sc_status_result={"success": False, "error": "timeout"},
        )

        result = svc.query_status("1")

        assert result["success"] is False


# ── publish_upgrade() ────────────────────────────────────────────────


class TestPublishUpgrade:
    def test_normal_upgrade_keeps_v1_published(self):
        """U1: PUBLISHED v1 → v1 stays PUBLISHED, new v2 DEVELOPING"""
        skill = _make_skill(status="PUBLISHED", version=1)
        svc, repo, _, _, _ = _make_service(skill=skill, all_skills=[skill])

        result = svc.publish_upgrade("1")

        assert result["success"] is True
        assert result["data"]["new_version"] == 2
        assert result["data"]["old_status"] == "PUBLISHED"
        assert result["data"]["new_status"] == "DEVELOPING"
        repo.update.assert_not_called()
        repo.create.assert_called_once()

    def test_non_published_cannot_upgrade(self):
        """U2: 非 PUBLISHED 不能升级"""
        skill = _make_skill(status="DEVELOPING")
        svc, _, _, _, _ = _make_service(skill=skill)

        result = svc.publish_upgrade("1")

        assert result["success"] is False
        assert "仅 PUBLISHED" in result["message"]

    def test_version_none_treated_as_1(self):
        """U3: version=None 视为 v1, 升级后 v2"""
        skill = _make_skill(status="PUBLISHED", version=None)
        svc, _, _, _, _ = _make_service(skill=skill, all_skills=[skill])

        result = svc.publish_upgrade("1")

        assert result["data"]["new_version"] == 2

    def test_new_version_inherits_fields(self):
        """U4: 新版本继承 name/skill_uuid/source_type 等"""
        skill = _make_skill(
            status="PUBLISHED", version=3,
            name="my-skill", skill_uuid="keep-uuid", source_type="online",
        )
        svc, repo, _, _, _ = _make_service(skill=skill, all_skills=[skill])

        svc.publish_upgrade("1")

        create_data = repo.create.call_args[0][0]
        assert create_data["name"] == "my-skill"
        assert create_data["skill_uuid"] == "keep-uuid"
        assert create_data["source_type"] == "online"
        assert create_data["version"] == 4
        assert create_data["status"] == "DEVELOPING"

    def test_create_fail_returns_error(self):
        """U5: create 返回 None 时报错"""
        skill = _make_skill(status="PUBLISHED", version=1)
        svc, _, _, _, _ = _make_service(skill=skill, all_skills=[skill], create_returns=None)
        svc._skill_repo.create.return_value = None

        result = svc.publish_upgrade("1")

        assert result["success"] is False
        assert "创建新版本" in result["message"]


# ── 卷瓜传播 ─────────────────────────────────────────────────────────


class TestPropagation:
    @patch.dict("os.environ", {"SERVER_ENV": "pre"})
    def test_upgrade_publish_triggers_propagation(self):
        """P1: version>1 升级发布成功 → 调用 propagate_on_upgrade"""
        skill = _make_skill(status="PENDING", version=2, skill_uuid="uuid-1")
        svc, _, _, prop, _ = _make_service(
            skill=skill,
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        svc.query_status("1")

        prop.propagate_on_upgrade.assert_called_once_with(
            skill_uuid="uuid-1", env="pre", new_version="2",
        )

    def test_first_publish_triggers_propagation(self):
        """P2: version=1 首次发布 → 也调传播，触发运行时加载"""
        skill = _make_skill(status="PENDING", version=1, skill_uuid="uuid-1")
        svc, _, _, prop, _ = _make_service(
            skill=skill,
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        svc.query_status("1")

        prop.propagate_on_upgrade.assert_called_once_with(
            skill_uuid="uuid-1", env="dev", new_version="1",
        )

    def test_missing_skill_uuid_skips_propagation(self):
        """P3: skill_uuid 为空跳过传播"""
        skill = _make_skill(status="PENDING", version=2, skill_uuid=None)
        svc, _, _, prop, _ = _make_service(
            skill=skill,
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        svc.query_status("1")

        prop.propagate_on_upgrade.assert_not_called()

    def test_no_propagation_service_skips(self):
        """P4: propagation_service=None → 不报错"""
        skill = _make_skill(status="PENDING", version=2, skill_uuid="uuid-1")
        svc, _, _, _, _ = _make_service(
            skill=skill,
            with_propagation=False,
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        result = svc.query_status("1")

        assert result["success"] is True

    def test_propagation_exception_non_blocking(self):
        """P5: 传播异常不阻塞 query_status 主流程"""
        skill = _make_skill(status="PENDING", version=2, skill_uuid="uuid-1")
        svc, _, _, prop, _ = _make_service(
            skill=skill,
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )
        prop.propagate_on_upgrade.side_effect = RuntimeError("RPC failed")

        result = svc.query_status("1")

        assert result["success"] is True
        assert result["data"]["local_status"] == "PUBLISHED"

    @patch.dict("os.environ", {"SERVER_ENV": "prod"})
    def test_removal_propagation(self):
        """P6: 直接传 skill_uuid → 调 propagate_on_removal"""
        svc, _, _, prop, _ = _make_service(skill=_make_skill(), with_propagation=True)

        svc.propagate_on_removal("uuid-del")

        prop.propagate_on_removal.assert_called_once_with(
            skill_uuid="uuid-del", env="prod",
        )

    def test_removal_empty_uuid_silent(self):
        """P7: 空 skill_uuid 静默返回"""
        svc, _, _, prop, _ = _make_service(skill=None, with_propagation=True)

        svc.propagate_on_removal("")

        prop.propagate_on_removal.assert_not_called()

    def test_removal_exception_non_blocking(self):
        """P8: 删除传播异常不报错"""
        svc, _, _, prop, _ = _make_service(skill=_make_skill(), with_propagation=True)
        prop.propagate_on_removal.side_effect = RuntimeError("RPC failed")

        svc.propagate_on_removal("uuid-del")


# ── 旧版本自动下线 ─────────────────────────────────────────────────


class TestOfflineOldVersions:
    def test_v2_published_offlines_v1(self):
        """O-V1: v2 发布成功时，同 skill_uuid 的 v1 PUBLISHED → OFFLINE"""
        v2 = _make_skill(id="2", status="PENDING", version=2, skill_uuid="uuid-1", bolt_id="bot-a")
        v1 = _make_skill(id="1", status="PUBLISHED", version=1, skill_uuid="uuid-1", bolt_id="bot-a")
        svc, repo, _, _, _ = _make_service(
            skill=v2,
            all_skills=[v1, v2],
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        svc.query_status("2")

        update_calls = repo.update.call_args_list
        assert any(c == (("1", {"status": "OFFLINE"}),) for c in update_calls)

    def test_first_publish_no_offline(self):
        """O-V2: 首次发布（v1）不触发 offline 逻辑"""
        v1 = _make_skill(id="1", status="PENDING", version=1, skill_uuid="uuid-1")
        svc, repo, _, _, _ = _make_service(
            skill=v1,
            all_skills=[v1],
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        svc.query_status("1")

        update_calls = repo.update.call_args_list
        assert len(update_calls) == 1
        assert update_calls[0] == (("1", {"status": "PUBLISHED", "git_path": "center://uuid-1"}),)

    def test_different_uuid_not_offlined(self):
        """O-V3: 不同 skill_uuid 的 PUBLISHED 不受影响"""
        v2 = _make_skill(id="2", status="PENDING", version=2, skill_uuid="uuid-A", bolt_id="bot-a")
        other = _make_skill(id="99", status="PUBLISHED", version=1, skill_uuid="uuid-B", bolt_id="bot-a")
        svc, repo, _, _, _ = _make_service(
            skill=v2,
            all_skills=[v2, other],
            sc_status_result={
                "success": True,
                "data": {"status": "PUBLISHED", "isCompleted": True, "isSuccess": True},
            },
        )

        svc.query_status("2")

        update_calls = repo.update.call_args_list
        assert not any(c[0][0] == "99" for c in update_calls)


# ── 打包上传 ─────────────────────────────────────────────────────────


class TestPackAndUpload:
    def test_with_oss_calls_put_and_sign(self, tmp_path):
        """O1: 有 OSS 时调 put_object + sign_url"""
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("test")

        skill = _make_skill(
            status="DEVELOPING", name="my-skill", git_path=str(skill_dir),
            link_name="my-skill", version=1,
        )
        svc, _, _, _, oss = _make_service(skill=skill)

        svc.publish("1")

        oss.put_object.assert_called_once()
        oss.sign_url.assert_called_once()
        oss_path = oss.put_object.call_args[0][0]
        assert oss_path.startswith("skill-publish/my-skill/")
        assert oss_path.endswith(".zip")

    def test_missing_dir_raises_file_not_found(self):
        """O2: 目录不存在 → FileNotFoundError (fail-fast)"""
        skill = _make_skill(
            status="DEVELOPING", git_path="git:///nonexistent/path",
        )
        svc, _, _, _, oss = _make_service(skill=skill)

        with pytest.raises(FileNotFoundError, match="技能目录不存在"):
            svc.publish("1")

        oss.put_object.assert_not_called()


class TestResolveSkillDir:
    """_resolve_skill_dir 路径解析。"""

    def test_git_protocol_resolves_to_skills_repo(self, tmp_path):
        """git://relative/path → {skills-repo}/relative/path"""
        skill_dir = tmp_path / "skills-repo" / "biz" / "my-skill"
        skill_dir.mkdir(parents=True)

        with patch(
            "agentclaw.community.core.skill_center.services.skill_publish_service.SkillPublishService._resolve_skill_dir"
        ) as mock_resolve:
            mock_resolve.return_value = skill_dir
            _ = SkillPublishService._resolve_skill_dir.__wrapped__(
                "git://biz/my-skill"
            ) if hasattr(SkillPublishService._resolve_skill_dir, "__wrapped__") else None

        real_result = SkillPublishService._resolve_skill_dir("git://biz/my-skill")
        assert str(real_result).endswith("biz/my-skill")

    def test_local_protocol_resolves(self):
        """local://skill-name → 以 skill-name 结尾的路径"""
        result = SkillPublishService._resolve_skill_dir("local://my-upload")
        assert str(result).endswith("my-upload")

    def test_absolute_path_passthrough(self, tmp_path):
        """绝对路径直接使用"""
        result = SkillPublishService._resolve_skill_dir(str(tmp_path))
        assert result == tmp_path

    def test_empty_returns_empty_path(self):
        """空字符串 → Path("")"""
        result = SkillPublishService._resolve_skill_dir("")
        assert result == Path("")

    def test_none_like_empty(self):
        """None/空 → Path("")"""
        result = SkillPublishService._resolve_skill_dir("")
        assert result == Path("")


# ── _get_env ─────────────────────────────────────────────────────────


class TestGetEnv:
    def test_server_env(self, monkeypatch):
        monkeypatch.setenv("SERVER_ENV", "pre")
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        assert SkillPublishService._get_env() == "pre"

    def test_fallback_to_real_server_env(self, monkeypatch):
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.setenv("REAL_SERVER_ENV", "prod")
        assert SkillPublishService._get_env() == "prod"

    def test_default_dev(self, monkeypatch):
        monkeypatch.delenv("SERVER_ENV", raising=False)
        monkeypatch.delenv("REAL_SERVER_ENV", raising=False)
        monkeypatch.delenv("ALIPAY_APP_ENV", raising=False)
        assert SkillPublishService._get_env() == "dev"
