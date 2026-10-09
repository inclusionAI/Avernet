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

from secbaas.community.api.bot_runtime import (
    BotBindingInfo,
    BotResponse,
    BotServiceError,
    SessionInfo,
)
from secbaas.community.core.service.bot_run import (
    BotEngineAdapterRegistry,
    BotServiceConfig,
    ClawBotService,
)
from secbaas.community.core.service.bot_run._async_chat_client import (
    AsyncChatClient,
    ConcurrentSessionError,
)
from secbaas.community.core.service.bot_run._async_chat_client_pool import (
    AsyncChatClientPool,
)
from secbaas.community.plugins.secret.stub import StubSecretStorePlugin

# ==================== Fixtures ====================

BOT_ID = "test-bot-000001"
ENTITY_ID = "test-entity-001"
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


def _make_context():
    from secbaas.community.api.bot_runtime import BotChatContext

    return BotChatContext(
        api_key_prefix="key-abc",
        app_id="",
        app_type="baas",
        tenant="",
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
        binding_id=100101,
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
        binding_id=100002,
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
        engine_adapter_registry=BotEngineAdapterRegistry({}),
    )


# ==================== BindingInfo Required Tests ====================


class TestBindingInfoRequired:
    """binding_info/metadata/context 已是协议必传参数（类型收紧），
    此处验证缺 sandbox_id 时的运行时校验仍生效。"""

    @pytest.mark.asyncio
    async def test_binding_info_without_sandbox_raises_error(self, service):
        binding = BotBindingInfo(
            bot_id=BOT_ID,
            entity_id=ENTITY_ID,
            sandbox_id=None,
            device_provider="baas",
            device_id=BAAS_DEVICE_ID,
        )
        with pytest.raises(BotServiceError, match="requires sandbox_id"):
            await service.create_session(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                metadata={},
                binding_info=binding,
                context=_make_context(),
            )

    @pytest.mark.asyncio
    async def test_missing_required_kwargs_raises_type_error(self, service):
        """缺 binding_info/context 必传参数时抛 TypeError（协议契约）。"""
        with pytest.raises(TypeError, match="missing"):
            await service.create_session(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                metadata={},
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
            metadata={},
            binding_info=arca_binding,
            context=_make_context(),
        )
        assert session is not None
        assert session.status == "active"

    @pytest.mark.asyncio
    async def test_baas_binding_no_sandbox_id_raises_error(self, service, baas_binding):
        with pytest.raises(BotServiceError, match="requires sandbox_id"):
            await service.create_session(
                bot_id=f"{BOT_ID}:{ENTITY_ID}",
                metadata={},
                binding_info=baas_binding,
                context=_make_context(),
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
            config=config,
            secret_store=_make_secret_store(),
            client_pool=mock_pool,
            engine_adapter_registry=BotEngineAdapterRegistry({}),
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
                "secbaas.community.core.service.bot_run._claw_service.AsyncSessionClient"
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
            config=config,
            secret_store=_make_secret_store(),
            client_pool=mock_pool,
            engine_adapter_registry=BotEngineAdapterRegistry({}),
        )
        with patch.object(
            svc, "_get_headers", return_value={"x-proxypass-token": "tk"}
        ):
            with patch(
                "secbaas.community.core.service.bot_run._claw_service.AsyncSessionClient"
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
                timeout=30.0,
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
                timeout=30.0,
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
            attachments=None,
        )

    @pytest.mark.asyncio
    async def test_send_message_success_with_context(
        self, service, arca_binding, mock_pool
    ):
        mock_client = AsyncMock()
        mock_client.send_message.return_value = ("resp with auth", [])
        mock_pool.get.return_value = mock_client

        from secbaas.community.api.bot_runtime import BotChatContext

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
                timeout=30.0,
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
            attachments=None,
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
                timeout=30.0,
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
            attachments=None,
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
                    timeout=30.0,
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
                    timeout=30.0,
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
            attachments=None,
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
                metadata={},
                binding_info=arca_binding,
                context=_make_context(),
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
            metadata={},
            binding_info=arca_binding,
            context=_make_context(),
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
            metadata={},
            binding_info=arca_binding,
            context=_make_context(),
        )
        assert session.session_id == SESSION_ID
        assert session.status == "active"


# ==================== TestListSessions ====================


class TestListSessions:
    """ClawBotService.list_sessions() coverage."""

    @pytest.mark.asyncio
    async def test_missing_sandbox_id(self, service, baas_binding):
        """ClawBotService requires sandbox_id in binding_info."""
        with pytest.raises(BotServiceError, match="ClawBotService requires sandbox_id"):
            await service.list_sessions(binding_info=baas_binding)

    @pytest.mark.asyncio
    async def test_success_path_with_pagination(
        self, service, arca_binding, monkeypatch
    ):
        """Successful list_sessions delegates to session client with correct args."""
        from secbaas.community.core.service.bot_run._async_session_client import (
            SessionInfo as AdapterSessionInfo,
        )

        adapter_session = AdapterSessionInfo(
            id="sess-001",
            title="test",
            user_id="user-1",
            agent_id=BOT_ID,
            created_at="2025-01-01T00:00:00Z",
            updated_at="2025-01-01T01:00:00Z",
        )

        session_client = AsyncMock()
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)
        session_client.list_sessions = AsyncMock(return_value=[adapter_session])

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        result = await service.list_sessions(
            binding_info=arca_binding,
            limit=5,
            offset=2,
        )

        session_client.list_sessions.assert_awaited_once_with(
            agent_id=BOT_ID,
            limit=5,
            offset=2,
            engine=arca_binding.engine_type,
        )
        assert len(result) == 1
        assert result[0].session_id == "sess-001"
        assert result[0].bot_id == BOT_ID

    @pytest.mark.asyncio
    async def test_downstream_client_failure(self, service, arca_binding, monkeypatch):
        """Downstream client RuntimeError wraps as BotServiceError."""
        session_client = AsyncMock()
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)
        session_client.list_sessions = AsyncMock(side_effect=RuntimeError("timeout"))

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        with pytest.raises(BotServiceError, match="Failed to list sessions"):
            await service.list_sessions(binding_info=arca_binding)

    @pytest.mark.asyncio
    async def test_bot_service_error_passthrough(
        self, service, arca_binding, monkeypatch
    ):
        """Existing BotServiceError passes through without re-wrapping."""
        session_client = AsyncMock()
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)
        session_client.list_sessions = AsyncMock(
            side_effect=BotServiceError("already wrapped"),
        )

        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)

        with pytest.raises(BotServiceError, match="already wrapped"):
            await service.list_sessions(binding_info=arca_binding)


# ==================== Session-pending materialization ====================


class TestSessionPendingMaterialization:
    """send/stream/inject 在 session_pending 时先物化 planned session。"""

    @pytest.mark.asyncio
    async def test_send_message_materializes_pending_session(
        self, service, arca_binding
    ):
        mat = AsyncMock()
        client = AsyncMock()
        client.send_message = AsyncMock(return_value=("hi", []))
        service._client_pool.get = AsyncMock(return_value=client)
        with patch.object(service, "_materialize_session", mat):
            resp = await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=arca_binding,
                context=_make_context(),
                timeout=1.0,
                session_pending=True,
            )
        assert resp.content == "hi"
        mat.assert_awaited_once()
        assert mat.await_args.kwargs["session_id"] == SESSION_ID

    @pytest.mark.asyncio
    async def test_send_message_stream_materializes_pending_session(
        self, service, arca_binding
    ):
        mat = AsyncMock()

        class _EmptyAsyncIter:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        client = MagicMock()
        client.send_message_stream = MagicMock(return_value=_EmptyAsyncIter())
        service._client_pool.get = AsyncMock(return_value=client)
        with patch.object(service, "_materialize_session", mat):
            async for _ in service.send_message_stream(
                session_id=SESSION_ID,
                message="hello",
                binding_info=arca_binding,
                context=_make_context(),
                timeout=1.0,
                session_pending=True,
            ):
                pass
        mat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_inject_message_materializes_pending_session(
        self, service, arca_binding
    ):
        mat = AsyncMock()
        client = AsyncMock()
        service._client_pool.get = AsyncMock(return_value=client)
        with patch.object(service, "_materialize_session", mat):
            await service.inject_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=arca_binding,
                context=_make_context(),
                session_pending=True,
            )
        mat.assert_awaited_once()


# ==================== Adapter routing & materialization ====================


class TestAdapterFor:
    """_adapter_for：仅 aicoding/hermes/claude_code 命中已注册 adapter。"""

    def test_returns_registered_adapter(self, service):
        registry = MagicMock()
        adapter = MagicMock()
        registry.has.return_value = True
        registry.get.return_value = adapter
        service._engine_adapter_registry = registry
        assert service._adapter_for("aicoding") is adapter

    def test_returns_none_when_not_registered(self, service):
        registry = MagicMock()
        registry.has.return_value = False
        service._engine_adapter_registry = registry
        assert service._adapter_for("openclaw") is None


class TestGetOrCreateAdapterSessionOpenclawPrefix:
    """新建 openclaw adapter session 补 agent:main: 前缀。"""

    @pytest.mark.asyncio
    async def test_openclaw_session_id_gets_agent_main_prefix(self, service):
        session_client = AsyncMock()
        adapter_session = MagicMock()
        adapter_session.id = "sess-1"
        session_client.create_session = AsyncMock(return_value=adapter_session)

        adapter_session_id, reused = await service._get_or_create_adapter_session(
            session_client=session_client,
            session_id=None,
            user_id="u-1",
            metadata={},
            engine_type="openclaw",
            bot_id=BOT_ID,
            run_id="run-1",
        )
        assert adapter_session_id == "agent:main:sess-1"
        assert reused is False


class TestMaterializeSession:
    """claw 版物化：用裸 key 在 adapter 侧创建 planned session。"""

    @pytest.mark.asyncio
    async def test_creates_with_bare_key(self, service, arca_binding, monkeypatch):
        session_client = AsyncMock()
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)
        session_client.create_session = AsyncMock()
        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)
        await service._materialize_session(
            session_id="agent:main:session:abc-1:user:u-1",
            binding_info=arca_binding,
            user_id="u-1",
            metadata={"title": "t"},
        )
        session_client.create_session.assert_awaited_once_with(
            title="t", user_id="u-1", model=None, uuid="abc-1"
        )

    @pytest.mark.asyncio
    async def test_wraps_failure_as_bot_service_error(
        self, service, arca_binding, monkeypatch
    ):
        session_client = AsyncMock()
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)
        session_client.create_session = AsyncMock(side_effect=RuntimeError("net"))
        monkeypatch.setattr(service, "_create_session_client", lambda x: session_client)
        with pytest.raises(BotServiceError, match="Failed to materialize"):
            await service._materialize_session(
                session_id="agent:main:session:abc-1:user:u-1",
                binding_info=arca_binding,
                user_id="u-1",
            )
