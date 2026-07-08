"""Unit tests for ClawBotService.

Covers:
- URL building (_build_base_url, _build_ws_url, _get_path_target)
- Proxy token & headers (secret_store.generate_proxy_token, _get_headers)
- Session client creation (_create_session_client)
- Adapter session management (_get_or_create_adapter_session)
- create_session: binding_info required validation, connection flow
- send_message: success, runtime error, binding_info validation
- inject_message: success, binding_info validation
- get_messages: success, binding_info validation
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_runtime import (
    BotBindingInfo,
    BotResponse,
    BotServiceError,
    SessionInfo,
)
from secbaas.core.service.bot_run import BotServiceConfig, ClawBotService
from secbaas.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    ConcurrentSessionError,
)
from secbaas.core.service.bot_run._async_chat_client_pool import AsyncChatClientPool
from secbaas.plugins.secret.stub import StubSecretStorePlugin

# ==================== Fixtures ====================

BOT_ID = "20260507_9szl2cmj"
ENTITY_ID = "397302"
SANDBOX_ID = "ARCA-SANDBOX-abc@0"
SANDOX_DEVICE_ID = "staff_395850_bot_uuid"
BAAS_DEVICE_ID = "301516dd13a942639420174eaa63190e"
SESSION_ID = "agent:main:sess-001"


def _make_config():
    return BotServiceConfig(
        proxy_base_url="https://proxy.test.com",
        proxy_ws_base_url="wss://proxy.test.com",
        adapter_port=20003,
    )


def _make_secret_store():
    return StubSecretStorePlugin()


@pytest.fixture
def arca_binding():
    return BotBindingInfo(
        bot_id=BOT_ID,
        entity_id=ENTITY_ID,
        sandbox_id=SANDBOX_ID,
        device_id=SANDOX_DEVICE_ID,
        device_provider="arca",
        binding_id=1333961,
        device_props={"sandbox_id": SANDBOX_ID},
        bot_type="personal",
    )


@pytest.fixture
def baas_binding():
    return BotBindingInfo(
        bot_id=BOT_ID,
        entity_id=ENTITY_ID,
        sandbox_id=None,
        device_id=BAAS_DEVICE_ID,
        device_provider="baas",
        binding_id=1333291,
        device_props={},
        bot_type="service",
    )


@pytest.fixture
def mock_pool():
    pool = MagicMock(spec=AsyncChatClientPool)
    pool.get = AsyncMock()
    return pool


@pytest.fixture
def service(mock_pool):
    return ClawBotService(
        config=_make_config(),
        client_pool=mock_pool,
        secret_store=_make_secret_store(),
    )


# ==================== BindingInfo Required Tests ====================


class TestBindingInfoRequired:
    @pytest.mark.asyncio
    async def test_missing_binding_info_raises_error(self, service):
        with pytest.raises(BotServiceError, match="requires binding_info"):
            await service.create_session(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
            )

    @pytest.mark.asyncio
    async def test_binding_info_none_raises_error(self, service):
        with pytest.raises(BotServiceError, match="requires binding_info"):
            await service.create_session(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                binding_info=None,
            )


# ==================== SandboxId Handling Tests ====================


class TestSandboxIdHandling:
    @pytest.mark.asyncio
    async def test_arca_binding_creates_session(
        self, service, arca_binding, monkeypatch
    ):
        session_client = AsyncMock()
        session_client.create_session.return_value = AsyncMock(
            id="agent:main:sess-arca"
        )
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        session = await service.create_session(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            binding_info=arca_binding,
        )
        assert session is not None
        assert session.status == "active"

    @pytest.mark.asyncio
    async def test_baas_binding_no_sandbox_id_raises_error(self, service, baas_binding):
        with pytest.raises(BotServiceError, match="requires sandbox_id"):
            await service.create_session(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                binding_info=baas_binding,
            )


# ==================== URL Building Tests ====================


class TestUrlBuilding:
    def test_get_path_target(self, service):
        result = service._get_path_target("sandbox-123")
        assert result == "ARCA_sandbox-123:20003"

    def test_get_path_target_different_port(self, mock_pool):
        config = BotServiceConfig(
            proxy_base_url="https://proxy.test.com",
            proxy_ws_base_url="wss://proxy.test.com",
            adapter_port=18789,
        )
        svc = ClawBotService(
            config=config, secret_store=_make_secret_store(), client_pool=mock_pool
        )
        result = svc._get_path_target("sb-abc")
        assert result == "ARCA_sb-abc:18789"

    def test_build_base_url(self, service):
        result = service._build_base_url("sandbox-xyz")
        assert result == "https://proxy.test.com/proxypass/ARCA_sandbox-xyz:20003"

    def test_build_ws_url(self, service):
        result = service._build_ws_url("sandbox-xyz")
        assert (
            result
            == "wss://proxy.test.com/proxypass/ARCA_sandbox-xyz:20003/api/openclaw/ws"
        )

    def test_build_urls_with_sandbox_id_containing_at_sign(self, service):
        result_base = service._build_base_url("ARCA-SANDBOX-abc@0")
        result_ws = service._build_ws_url("ARCA-SANDBOX-abc@0")
        assert "ARCA-SANDBOX-abc@0:20003" in result_base
        assert "ARCA-SANDBOX-abc@0:20003/api/openclaw/ws" in result_ws


# ==================== Proxy Token & Headers Tests ====================


class TestProxyTokenAndHeaders:
    def test_get_headers_uses_secret_store(self, service):
        """_get_headers should delegate to secret_store.generate_proxy_token."""
        mock_secret_store = MagicMock()
        mock_secret_store.generate_proxy_token.return_value = "fake.jwt.token.here"

        service._secret_store = mock_secret_store
        headers = service._get_headers("sandbox-1")

        assert headers == {"x-proxypass-token": "fake.jwt.token.here"}
        mock_secret_store.generate_proxy_token.assert_called_once_with(
            target="ARCA_sandbox-1:20003"
        )

    def test_get_headers_calls_generate_proxy_token_with_correct_target(self, service):
        mock_secret_store = MagicMock()
        mock_secret_store.generate_proxy_token.return_value = "token-xyz"

        service._secret_store = mock_secret_store
        headers = service._get_headers("sb-99")

        assert "x-proxypass-token" in headers
        assert headers["x-proxypass-token"] == "token-xyz"
        mock_secret_store.generate_proxy_token.assert_called_once_with(
            target="ARCA_sb-99:20003"
        )


# ==================== Session Client Creation Tests ====================


class TestSessionClientCreation:
    def test_create_session_client_builds_correctly(self, service):
        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            with patch(
                "secbaas.core.service.bot_run._claw_service.AsyncSessionClient"
            ) as mock_asc:
                fake_client = MagicMock()
                mock_asc.return_value = fake_client

                result = service._create_session_client("sb-test")
                assert result is fake_client
                mock_asc.assert_called_once_with(
                    base_url="https://proxy.test.com/proxypass/ARCA_sb-test:20003",
                    headers={"x-proxypass-token": "tk"},
                    timeout=30,
                )

    def test_create_session_client_uses_configured_timeout(self, mock_pool):
        config = BotServiceConfig(
            proxy_base_url="https://proxy.test.com",
            proxy_ws_base_url="wss://proxy.test.com",
            adapter_port=20003,
            request_timeout=60,
        )
        svc = ClawBotService(
            config=config, secret_store=_make_secret_store(), client_pool=mock_pool
        )
        with patch.object(
            svc, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            with patch(
                "secbaas.core.service.bot_run._claw_service.AsyncSessionClient"
            ) as mock_asc:
                svc._create_session_client("sb-t")
                mock_asc.assert_called_once()
                call_kwargs = mock_asc.call_args.kwargs
                assert call_kwargs["timeout"] == 60


# ==================== Send Message Tests ====================


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_message_no_sandbox_id_raises_error(self, service, baas_binding):
        with pytest.raises(BotServiceError, match="requires sandbox_id"):
            await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=baas_binding,
            )

    @pytest.mark.asyncio
    async def test_send_message_success(self, service, arca_binding, mock_pool):
        mock_client = AsyncMock()
        mock_client.send_message.return_value = ("response text", [])
        mock_pool.get.return_value = mock_client

        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            result = await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=arca_binding,
                wait_result=True,
            )

        assert isinstance(result, BotResponse)
        assert result.content == "response text"
        mock_pool.get.assert_awaited_once()
        mock_client.send_message.assert_awaited_once_with(
            message="hello",
            session_key=SESSION_ID,
            wait_result=True,
            timeout=30,
            auth_token=None,
            app_id=None,
            chat_metadata=None,
        )

    @pytest.mark.asyncio
    async def test_send_message_success_with_context(
        self, service, arca_binding, mock_pool
    ):
        mock_client = AsyncMock()
        mock_client.send_message.return_value = ("resp with auth", [])
        mock_pool.get.return_value = mock_client

        from secbaas.api.bot_runtime import BotChatContext

        ctx = BotChatContext(
            api_key_prefix="prefix-1",
            app_id="app-1",
            app_type="web",
        )

        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            result = await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=arca_binding,
                context=ctx,
            )

        assert result.content == "resp with auth"
        mock_client.send_message.assert_awaited_once_with(
            message="hello",
            session_key=SESSION_ID,
            wait_result=True,
            timeout=30,
            auth_token="OPEN_API:app:prefix-1",
            app_id="app-1",
            chat_metadata=None,
        )

    @pytest.mark.asyncio
    async def test_send_message_wait_result_false(
        self, service, arca_binding, mock_pool
    ):
        mock_client = AsyncMock()
        mock_client.send_message.return_value = ("fast resp", [])
        mock_pool.get.return_value = mock_client

        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            result = await service.send_message(
                session_id=SESSION_ID,
                message="ping",
                binding_info=arca_binding,
                wait_result=False,
            )

        assert result.content == "fast resp"
        mock_client.send_message.assert_awaited_once_with(
            message="ping",
            session_key=SESSION_ID,
            wait_result=False,
            timeout=30,
            auth_token=None,
            app_id=None,
            chat_metadata=None,
        )

    @pytest.mark.asyncio
    async def test_send_message_runtime_error_releases_client(
        self, service, arca_binding, mock_pool
    ):
        mock_client = AsyncMock()
        mock_client.send_message.side_effect = RuntimeError("ws disconnected")
        mock_pool.get.return_value = mock_client

        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            with pytest.raises(BotServiceError, match="Failed to send message"):
                await service.send_message(
                    session_id=SESSION_ID,
                    message="boom",
                    binding_info=arca_binding,
                )

        # No release needed — shared connection stays in pool

    @pytest.mark.asyncio
    async def test_send_message_concurrent_session_rejected(
        self, service, arca_binding, mock_pool
    ):
        """Concurrent send_message on the same session_key should raise BotServiceError."""
        mock_client = AsyncMock()
        mock_client.send_message.side_effect = ConcurrentSessionError(
            "Concurrent send_message on session_key=test-session is not allowed"
        )
        mock_pool.get.return_value = mock_client

        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            with pytest.raises(BotServiceError, match="Concurrent request"):
                await service.send_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=arca_binding,
                )


# ==================== Inject Message Tests ====================


class TestInjectMessage:
    @pytest.mark.asyncio
    async def test_inject_message_no_sandbox_id_raises_error(
        self, service, baas_binding
    ):
        with pytest.raises(BotServiceError, match="requires sandbox_id"):
            await service.inject_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=baas_binding,
            )

    @pytest.mark.asyncio
    async def test_inject_message_success(self, service, arca_binding, mock_pool):
        mock_client = AsyncMock()
        mock_pool.get.return_value = mock_client

        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            await service.inject_message(
                session_id=SESSION_ID,
                message="system instruction",
                binding_info=arca_binding,
            )

        mock_pool.get.assert_awaited_once()
        mock_client.inject_message.assert_awaited_once_with(
            message="system instruction",
            session_key=SESSION_ID,
            auth_token=None,
        )

    @pytest.mark.asyncio
    async def test_inject_message_error_releases_client(
        self, service, arca_binding, mock_pool
    ):
        mock_client = AsyncMock()
        mock_client.inject_message.side_effect = RuntimeError("ws fail")
        mock_pool.get.return_value = mock_client

        with patch.object(
            service, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            with pytest.raises(BotServiceError, match="Failed to inject message"):
                await service.inject_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=arca_binding,
                )

        # No release needed — shared connection stays in pool


# ==================== Get Messages Tests ====================


class TestGetMessages:
    @pytest.mark.asyncio
    async def test_get_messages_no_sandbox_id_raises_error(self, service, baas_binding):
        with pytest.raises(BotServiceError, match="requires sandbox_id"):
            await service.get_messages(
                session_id=SESSION_ID,
                binding_info=baas_binding,
            )

    @pytest.mark.asyncio
    async def test_get_messages_success(self, service, arca_binding, monkeypatch):
        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.session_id = SESSION_ID
        mock_msg.role = "user"
        mock_msg.content = "hello"
        mock_msg.meta = {}
        mock_msg.created_at = datetime.now()
        mock_msg.history_meta = None

        session_client = AsyncMock()
        session_client.get_messages.return_value = [mock_msg]
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        result = await service.get_messages(
            session_id=SESSION_ID,
            binding_info=arca_binding,
        )

        assert len(result) == 1
        assert result[0].content == "hello"


# ==================== Create Session Comprehensive Tests ====================


class TestCreateSessionFullFlow:
    @pytest.mark.asyncio
    async def test_create_session_adapter_session_error_wraps(
        self, service, arca_binding, monkeypatch
    ):
        session_client = AsyncMock()
        session_client.create_session.side_effect = RuntimeError("adapter down")
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        with pytest.raises(
            BotServiceError, match="Failed to get or create adapter session"
        ):
            await service.create_session(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                binding_info=arca_binding,
            )

    @pytest.mark.asyncio
    async def test_create_session_creates_adapter_session(
        self, service, arca_binding, monkeypatch
    ):
        fake_session = MagicMock()
        fake_session.id = "agent:main:new-sess"

        session_client = AsyncMock()
        session_client.create_session.return_value = fake_session
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        session = await service.create_session(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            binding_info=arca_binding,
        )
        assert session is not None
        assert session.session_id == "agent:main:new-sess"
        assert session.status == "active"

    @pytest.mark.asyncio
    async def test_create_session_reuse_existing_session_id(
        self, service, arca_binding, monkeypatch
    ):
        session_client = AsyncMock()
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        session = await service.create_session(
            bot_id=f"{BOT_ID}:{ENTITY_ID}",
            session_id=SESSION_ID,
            binding_info=arca_binding,
        )
        assert session.session_id == SESSION_ID
        assert session.status == "active"
