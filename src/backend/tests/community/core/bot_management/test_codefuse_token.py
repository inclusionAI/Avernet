"""Unit tests for ``core.bot_management.codefuse_token``（下沉的可复用工具）。

覆盖：
- ``decode_auth_code``：合法解码 + 各类非法输入抛 ``ValueError``（无 FastAPI 依赖）。
- ``build_codefuse_write_cmd``：命令含 mkdir / python3 / 路径；base64 patch 含
  token/workid/authType=OAUTH；末尾读回校验。
- ``build_codefuse_write_cmd_from_auth_code``：解码 + 构建一体。
"""
from __future__ import annotations

import base64
import json

import pytest

from agentclaw.community.core.bot_management import codefuse_token as cft


def _encode_auth_code(token: str, workid: str) -> str:
    return base64.b64encode(json.dumps({"t": token, "w": workid}).encode()).decode()


class TestDecodeAuthCode:
    def test_valid(self):
        token, workid = cft.decode_auth_code(_encode_auth_code("a" * 32, "u001"))
        assert token == "a" * 32
        assert workid == "u001"

    def test_hex_16_boundary_ok(self):
        token, workid = cft.decode_auth_code(_encode_auth_code("a" * 16, "u001"))
        assert token == "a" * 16

    def test_invalid_base64_raises_valueerror(self):
        with pytest.raises(ValueError, match="base64"):
            cft.decode_auth_code("!!!not-base64!!!")

    def test_valid_base64_not_json(self):
        auth_code = base64.b64encode(b"not-json").decode()
        with pytest.raises(ValueError, match="JSON"):
            cft.decode_auth_code(auth_code)

    def test_json_not_object(self):
        auth_code = base64.b64encode(json.dumps([1, 2]).encode()).decode()
        with pytest.raises(ValueError, match="expected JSON object"):
            cft.decode_auth_code(auth_code)

    def test_missing_token(self):
        with pytest.raises(ValueError, match="missing token"):
            cft.decode_auth_code(_encode_auth_code("", "u001"))

    def test_token_too_short(self):
        with pytest.raises(ValueError, match="too short"):
            cft.decode_auth_code(_encode_auth_code("abc123", "u001"))

    def test_token_not_hex(self):
        with pytest.raises(ValueError, match="hex"):
            cft.decode_auth_code(_encode_auth_code("z" * 16, "u001"))

    def test_missing_workid(self):
        with pytest.raises(ValueError, match="missing workid"):
            cft.decode_auth_code(_encode_auth_code("a" * 32, ""))


class TestBuildCodefuseWriteCmd:
    def test_contains_key_parts(self):
        cmd = cft.build_codefuse_write_cmd("abcdef0123456789", "u001")
        assert "mkdir -p /home/admin/.codefuse/fuse" in cmd
        assert "python3 -c" in cmd
        assert cft.CODEFUSE_JSON_PATH in cmd

    def test_patch_includes_token_workid_oauth(self):
        cmd = cft.build_codefuse_write_cmd("abcdef0123456789", "u001")
        import re

        match = re.search(r"b64decode\('([^']+)'\)", cmd)
        assert match, "未在命令中找到 base64 patch"
        patch = json.loads(base64.b64decode(match.group(1)).decode())
        assert patch["token"] == "abcdef0123456789"
        assert patch["workid"] == "u001"
        assert patch["authType"] == "OAUTH"

    def test_verifies_readable(self):
        cmd = cft.build_codefuse_write_cmd("a" * 32, "w")
        assert "open(p).read()" in cmd


class TestBuildFromAuthCode:
    def test_decode_then_build(self):
        auth_code = _encode_auth_code("abcdef0123456789", "u001")
        cmd = cft.build_codefuse_write_cmd_from_auth_code(auth_code)
        assert "codefuse.json" in cmd
        import re

        match = re.search(r"b64decode\('([^']+)'\)", cmd)
        patch = json.loads(base64.b64decode(match.group(1)).decode())
        assert patch["token"] == "abcdef0123456789"
        assert patch["workid"] == "u001"

    def test_invalid_auth_code_raises(self):
        with pytest.raises(ValueError):
            cft.build_codefuse_write_cmd_from_auth_code("!!!bad!!!")


# ── resolve_codefuse_runtime_binding_ids ──────────────────────────────

from unittest.mock import MagicMock, patch  # noqa: E402

from agentclaw.community.core.bot_management.codefuse_runtime_targets import (  # noqa: E402
    resolve_codefuse_runtime_binding_ids,
)


def _pub_record(status, ext_binding, publish_id=1):
    rec = MagicMock()
    rec.id = publish_id
    rec.status = status
    rec.ext = {"binding": ext_binding}
    return rec


def _active_binding(binding_id):
    b = MagicMock()
    b.id = binding_id
    b.status = "ACTIVE"
    return b


class TestResolveCodefuseRuntimeBindingIds:
    """服务 bot 三运行时拓扑解析；verify/online 复用 resolve_stage_bind_id。"""

    def test_personal_returns_draft_only_and_skips_publish(self):
        publish_repo = MagicMock()
        binding_repo = MagicMock()
        ids = resolve_codefuse_runtime_binding_ids(
            bot={"id": 1, "bot_id": "p1", "owner_id": "u", "binding_id": 10, "bot_type": "personal"},
            publish_repo=publish_repo, binding_repo=binding_repo,
        )
        assert ids == [10]
        publish_repo.list_by_source_bot.assert_not_called()

    def test_personal_default_when_bot_type_missing(self):
        publish_repo = MagicMock()
        ids = resolve_codefuse_runtime_binding_ids(
            bot={"id": 1, "bot_id": "p2", "owner_id": "u", "binding_id": 7},
            publish_repo=MagicMock(), binding_repo=MagicMock(),
        )
        assert ids == [7]

    def test_service_fans_out_draft_verify_online(self):
        # 一条 VALIDATING(verify=2) + 一条 SUCCESS(online=3)
        publish_repo = MagicMock()
        publish_repo.list_by_source_bot.return_value = [
            _pub_record("validating", {"verify": 2}),
            _pub_record("success", {"online": 3}),
        ]
        ids = resolve_codefuse_runtime_binding_ids(
            bot={"id": 100, "bot_id": "s1", "owner_id": "u", "binding_id": 1, "bot_type": "service"},
            publish_repo=publish_repo, binding_repo=MagicMock(),
        )
        # draft(1) + verify(2) + online(3)
        assert ids == [1, 2, 3]

    def test_service_retained_verify_after_promotion(self):
        """已全量上线（只有 SUCCESS、无 VALIDATING）但 verify runtime 仍 ACTIVE：
        resolve_stage_bind_id(verify) 回退到 SUCCESS 记录的 verify binding ——
        旧实现（照搬 passport）会漏掉这个 retained verify 容器。"""
        publish_repo = MagicMock()
        publish_repo.list_by_source_bot.return_value = [
            _pub_record("success", {"online": 9, "verify": 8}),
        ]
        binding_repo = MagicMock()
        binding_repo.get_by_id.return_value = _active_binding(8)  # retained verify 仍 ACTIVE

        ids = resolve_codefuse_runtime_binding_ids(
            bot={"id": 100, "bot_id": "s2", "owner_id": "u", "binding_id": None, "bot_type": "service"},
            publish_repo=publish_repo, binding_repo=binding_repo,
        )
        assert ids == [8, 9]  # retained verify(8) + online(9)

    def test_service_no_records_returns_empty(self):
        publish_repo = MagicMock()
        publish_repo.list_by_source_bot.return_value = []
        ids = resolve_codefuse_runtime_binding_ids(
            bot={"id": 100, "bot_id": "s3", "owner_id": "u", "binding_id": None, "bot_type": "service"},
            publish_repo=publish_repo, binding_repo=MagicMock(),
        )
        assert ids == []

    def test_service_dedups_draft_and_online(self):
        publish_repo = MagicMock()
        publish_repo.list_by_source_bot.return_value = [
            _pub_record("success", {"online": 1}),
        ]
        ids = resolve_codefuse_runtime_binding_ids(
            bot={"id": 100, "bot_id": "s4", "owner_id": "u", "binding_id": 1, "bot_type": "service"},
            publish_repo=publish_repo, binding_repo=MagicMock(),
        )
        assert ids == [1]

    def test_service_publish_lookup_error_skipped(self):
        publish_repo = MagicMock()
        publish_repo.list_by_source_bot.side_effect = RuntimeError("boom")
        ids = resolve_codefuse_runtime_binding_ids(
            bot={"id": 100, "bot_id": "s5", "owner_id": "u", "binding_id": 1, "bot_type": "service"},
            publish_repo=publish_repo, binding_repo=MagicMock(),
        )
        # draft 仍返回；发布态查询失败被吞
        assert ids == [1]

    # ── 防御性 guard：非法 binding_id 不阻塞其它 runtime ──────────────────

    def test_non_int_draft_binding_id_skipped(self):
        """draft binding_id 非 int（脏数据）→ 告警跳过，其余 runtime 仍解析。"""
        with patch(
            "agentclaw.community.core.engine_runtime.stage.resolve_stage_bind_id",
            return_value=5,
        ):
            ids = resolve_codefuse_runtime_binding_ids(
                bot={"id": 100, "bot_id": "s6", "owner_id": "u",
                     "binding_id": "not-int", "bot_type": "service"},
                publish_repo=MagicMock(), binding_repo=MagicMock(),
            )
        # 脏 draft 被吞；verify/online 返回 5
        assert ids == [5]

    def test_empty_resolved_bid_skipped(self):
        """resolve_stage_bind_id 返回空值 → 跳过该 stage（不 NPE）。"""
        with patch(
            "agentclaw.community.core.engine_runtime.stage.resolve_stage_bind_id",
            return_value="",
        ):
            ids = resolve_codefuse_runtime_binding_ids(
                bot={"id": 100, "bot_id": "s7", "owner_id": "u",
                     "binding_id": 1, "bot_type": "service"},
                publish_repo=MagicMock(), binding_repo=MagicMock(),
            )
        # 仅 draft；verify/online 返回空被跳过
        assert ids == [1]

    def test_non_int_resolved_bid_skipped(self):
        """resolve_stage_bind_id 返回非 int → 告警跳过。"""
        with patch(
            "agentclaw.community.core.engine_runtime.stage.resolve_stage_bind_id",
            return_value="abc-not-number",
        ):
            ids = resolve_codefuse_runtime_binding_ids(
                bot={"id": 100, "bot_id": "s8", "owner_id": "u",
                     "binding_id": None, "bot_type": "service"},
                publish_repo=MagicMock(), binding_repo=MagicMock(),
            )
        # draft 无；verify/online 非 int 被跳过
        assert ids == []

    def test_non_dict_bot_returns_empty(self):
        """bot 非 dict（类型错误防御）→ 返回空列表。"""
        ids = resolve_codefuse_runtime_binding_ids(
            bot=None,
            publish_repo=MagicMock(), binding_repo=MagicMock(),
        )
        assert ids == []
