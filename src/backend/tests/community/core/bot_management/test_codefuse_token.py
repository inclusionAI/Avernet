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
