"""Tests for ``PUT /api/aicoding/bots/{bot_id}/codefuse/auth``."""
from __future__ import annotations

import base64
import json
from dataclasses import replace as dataclass_replace
from datetime import datetime
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi_injector import attach_injector
from injector import Injector, Module, provider, singleton

from agentclaw.community.adapters.http.aicoding.router import (
    router,
    _decode_auth_code,
    _build_codefuse_write_cmd,
    _CODEFUSE_JSON_PATH,
)
from agentclaw.community.adapters.http.auth.dependencies import get_current_user
from agentclaw.community.adapters.http.auth.models import AuthenticatedUser
from agentclaw.community.api.baas_service import BaasServiceProtocol
from agentclaw.community.api.device_service import DeviceServiceProtocol
from agentclaw.community.core.bot_management.repository.protocol import BotRepository
from agentclaw.community.core.devices.repository.protocol import DeviceBindingRepository
from agentclaw.community.core.devices.repository.record import DeviceBindingRecord


def _mock_user():
    return AuthenticatedUser(
        id="1", operatorName="test_user", staffId="u001", nickName="Tester"
    )


def _binding_record(
    device_provider: str = "baas",
    bot_uuid: str = "BOT-123",
) -> DeviceBindingRecord:
    return DeviceBindingRecord(
        id=1,
        entity_id="u001",
        entity_type="staff",
        device_id="staff_u001_bot1_abc",
        device_provider=device_provider,
        env="dev",
        device_props={"bot_uuid": bot_uuid},
        status="ACTIVE",
        apply_reason=None,
        applied_by="u001",
        release_reason=None,
        released_by=None,
        released_at=None,
        last_alive_at=None,
        gmt_create=datetime(2024, 1, 1),
        gmt_modified=datetime(2024, 1, 1),
    )


def _make_client(
    bot_repo=None,
    device_repo=None,
    baas_service=None,
    device_service=None,
) -> TestClient:
    _bot_repo = bot_repo or MagicMock()
    _device_repo = device_repo or MagicMock()
    _baas = baas_service or MagicMock()
    _device_svc = device_service or MagicMock()

    class _TestModule(Module):
        @provider
        @singleton
        def provide_bot_repo(self) -> BotRepository:
            return _bot_repo

        @provider
        @singleton
        def provide_device_repo(self) -> DeviceBindingRepository:
            return _device_repo

        @provider
        @singleton
        def provide_baas(self) -> BaasServiceProtocol:
            return _baas

        @provider
        @singleton
        def provide_device_service(self) -> DeviceServiceProtocol:
            return _device_svc

    app = FastAPI()
    app.include_router(router)
    injector = Injector([_TestModule()])
    attach_injector(app, injector)
    app.dependency_overrides[get_current_user] = _mock_user

    return TestClient(app)


def _encode_auth_code(token: str, workid: str) -> str:
    """Helper: build a base64 auth_code like the SSO callback returns."""
    return base64.b64encode(json.dumps({"t": token, "w": workid}).encode()).decode()


# ── auth_code decoding & validation ──────────────────────────────────────


class TestDecodeAuthCode:
    """Unit tests for ``_decode_auth_code``."""

    def test_valid_auth_code(self):
        token, workid = _decode_auth_code(_encode_auth_code("a" * 32, "u001"))
        assert token == "a" * 32
        assert workid == "u001"

    def test_invalid_base64(self):
        """Non-base64 string → 400."""
        try:
            _decode_auth_code("!!!not-base64!!!")
        except Exception as e:
            assert e.status_code == 400
            assert "base64" in e.detail.lower()
        else:
            raise AssertionError("expected HTTPException")

    def test_valid_base64_but_not_json(self):
        """Valid base64, non-JSON payload → 400."""
        auth_code = base64.b64encode(b"not-json").decode()
        try:
            _decode_auth_code(auth_code)
        except Exception as e:
            assert e.status_code == 400
            assert "json" in e.detail.lower()
        else:
            raise AssertionError("expected HTTPException")

    def test_json_not_object(self):
        """Valid base64 + JSON array (not object) → 400."""
        auth_code = base64.b64encode(json.dumps([1, 2]).encode()).decode()
        try:
            _decode_auth_code(auth_code)
        except Exception as e:
            assert e.status_code == 400
            assert "expected json object" in e.detail.lower()
        else:
            raise AssertionError("expected HTTPException")

    def test_missing_token(self):
        """Missing 't' field → 400."""
        auth_code = _encode_auth_code("", "u001")
        try:
            _decode_auth_code(auth_code)
        except Exception as e:
            assert e.status_code == 400
            assert "missing token" in e.detail.lower()
        else:
            raise AssertionError("expected HTTPException")

    def test_token_too_short(self):
        """Token < 16 chars → 400."""
        auth_code = _encode_auth_code("abc123", "u001")
        try:
            _decode_auth_code(auth_code)
        except Exception as e:
            assert e.status_code == 400
            assert "too short" in e.detail.lower()
        else:
            raise AssertionError("expected HTTPException")

    def test_token_not_hex(self):
        """Token not hex → 400."""
        auth_code = _encode_auth_code("zzzzzzzzzzzzzzzz", "u001")
        try:
            _decode_auth_code(auth_code)
        except Exception as e:
            assert e.status_code == 400
            assert "hex" in e.detail.lower()
        else:
            raise AssertionError("expected HTTPException")

    def test_missing_workid(self):
        """Missing 'w' field → 400."""
        auth_code = _encode_auth_code("a" * 32, "")
        try:
            _decode_auth_code(auth_code)
        except Exception as e:
            assert e.status_code == 400
            assert "missing workid" in e.detail.lower()
        else:
            raise AssertionError("expected HTTPException")

    def test_hex_16_chars_boundary(self):
        """Exactly 16 hex chars is accepted."""
        token, workid = _decode_auth_code(_encode_auth_code("a" * 16, "u001"))
        assert token == "a" * 16
        assert workid == "u001"


# ── _build_codefuse_write_cmd ────────────────────────────────────────────


class TestBuildCodefuseWriteCmd:
    """Unit tests for ``_build_codefuse_write_cmd``."""

    def test_command_contains_key_parts(self):
        cmd = _build_codefuse_write_cmd("abcdef0123456789", "u001", "owner1")
        # mkdir, python3, codefuse.json path, base64 patch
        assert "mkdir -p /home/admin/.codefuse/fuse" in cmd
        assert "python3 -c" in cmd
        assert _CODEFUSE_JSON_PATH in cmd

    def test_command_patch_includes_oauth(self):
        """The base64-encoded patch must contain authType OAUTH."""
        cmd = _build_codefuse_write_cmd("abcdef0123456789", "u001", "owner1")
        # Decode the base64 patch embedded in the command
        # The patch is between b64decode('...') and ).decode()
        import re as _re
        match = _re.search(r"b64decode\('([^']+)'\)", cmd)
        assert match, "Could not find base64 patch in command"
        patch = json.loads(base64.b64decode(match.group(1)).decode())
        assert patch["token"] == "abcdef0123456789"
        assert patch["workid"] == "u001"
        assert patch["authType"] == "OAUTH"

    def test_command_verifies_readable(self):
        """Command ends with a read-back verification step."""
        cmd = _build_codefuse_write_cmd("a" * 32, "w", "o")
        assert "open(p).read()" in cmd


# ── BaaS / poolab path ──────────────────────────────────────────────────


class TestCodefuseTokenBaas:
    """PUT /api/aicoding/bots/{bot_id}/codefuse/auth — BaaS provider."""

    def test_baas_success(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 1}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("baas", "BOT-XYZ")

        baas = MagicMock()
        baas.exec_command_on_bot.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo, baas_service=baas)
        auth_code = _encode_auth_code("abcdef0123456789" * 2, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["bot_id"] == "bot1"
        assert data["provider"] == "baas"

        baas.exec_command_on_bot.assert_called_once()
        call_kwargs = baas.exec_command_on_bot.call_args.kwargs
        assert call_kwargs["bot_uuid"] == "BOT-XYZ"
        # Command writes codefuse.json and includes the decoded token/workid
        cmd = call_kwargs["cmd"]
        assert "codefuse.json" in cmd
        assert "mkdir -p /home/admin/.codefuse/fuse" in cmd

    def test_baas_exec_failure_returns_502(self):
        from agentclaw.community.core.service_bot.services.baas_service import BaasServiceError

        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 1}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("baas")

        baas = MagicMock()
        baas.exec_command_on_bot.side_effect = BaasServiceError("timeout")

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo, baas_service=baas)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 502

    def test_baas_nonzero_exit_code_returns_502(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 1}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("baas")

        baas = MagicMock()
        baas.exec_command_on_bot.return_value = {"exit_code": 1, "stderr": "permission denied"}

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo, baas_service=baas)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 502
        assert "exit" in resp.json()["detail"].lower()

    def test_baas_no_bot_uuid_returns_400(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 1}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("baas", bot_uuid="")

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 400

    def test_baas_non_dict_result_returns_502(self):
        """When BaaS returns a non-dict result (e.g. string), exit_code defaults to -1 → 502."""
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 1}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("baas")

        baas = MagicMock()
        baas.exec_command_on_bot.return_value = "unexpected string result"

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo, baas_service=baas)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 502
        assert "exit" in resp.json()["detail"].lower()

    def test_baas_device_props_none_returns_400(self):
        """device_props=None should fall through to empty dict, bot_uuid missing → 400."""
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 1}

        binding = dataclass_replace(_binding_record("baas"), device_props=None)

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = binding

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 400


# ── Arca path ────────────────────────────────────────────────────────────


class TestCodefuseTokenArca:
    """PUT /api/aicoding/bots/{bot_id}/codefuse/auth — Arca provider."""

    def test_arca_success(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot2", "binding_id": 2}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("arca")

        device_svc = MagicMock()
        device_svc.exec_shell.return_value = "ok"

        client = _make_client(
            bot_repo=bot_repo, device_repo=device_repo, device_service=device_svc
        )
        auth_code = _encode_auth_code("deadbeef12345678" * 2, "u001")
        resp = client.put("/api/aicoding/bots/bot2/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["provider"] == "arca"

        device_svc.exec_shell.assert_called_once()
        call_kwargs = device_svc.exec_shell.call_args.kwargs
        assert call_kwargs["device_id"] == "staff_u001_bot1_abc"
        shell_cmd = call_kwargs["shell_cmd"]
        assert "codefuse.json" in shell_cmd
        assert "mkdir -p /home/admin/.codefuse/fuse" in shell_cmd

    def test_arca_exec_failure_returns_502(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot2", "binding_id": 2}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("arca")

        device_svc = MagicMock()
        device_svc.exec_shell.side_effect = RuntimeError("sandbox error")

        client = _make_client(
            bot_repo=bot_repo, device_repo=device_repo, device_service=device_svc
        )
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot2/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 502


# ── Common error cases ───────────────────────────────────────────────────


class TestCodefuseTokenCommon:
    """PUT /api/aicoding/bots/{bot_id}/codefuse/auth — common error cases."""

    def test_bot_not_found_or_not_owner_returns_404(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = None

        client = _make_client(bot_repo=bot_repo)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/missing/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 404

    def test_no_binding_returns_400(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1"}

        client = _make_client(bot_repo=bot_repo)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 400

    def test_binding_not_found_returns_404(self):
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 99}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = None

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo)
        auth_code = _encode_auth_code("a" * 32, "u001")
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": auth_code})

        assert resp.status_code == 404

    def test_invalid_auth_code_returns_400(self):
        """Passing a plain string (not base64 auth_code) returns 400."""
        bot_repo = MagicMock()
        bot_repo.get_by_id_and_owner.return_value = {"bot_id": "bot1", "binding_id": 1}

        device_repo = MagicMock()
        device_repo.get_by_id.return_value = _binding_record("baas")

        client = _make_client(bot_repo=bot_repo, device_repo=device_repo)
        resp = client.put("/api/aicoding/bots/bot1/codefuse/auth", json={"token": "plain-text-not-base64"})

        assert resp.status_code == 400
        assert "auth_code" in resp.json()["detail"].lower()