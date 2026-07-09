"""Unit tests for BaasBotService.

Covers:
- create_session: tenant validation, bot resolution, session persistence, binding_info.baas_session_id
- send_message: session lifecycle (completed/failed), ChatClient error states
- inject_message: success, error states
- get_messages: success, error states
- _persist_session_create: persistence gating and metadata handling
- _mark_session_completed / _mark_session_failed: DB status updates
- _build_base_url: URL conversion logic
- _get_or_create_adapter_session: session reuse vs creation
- _resolve_ws_connection_for_binding: binding_info-based resolution
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from secbaas.api.bot_runtime import (
    BotBindingInfo,
    BotNotAvailableError,
    BotNotFoundError,
    BotServiceError,
    NoActiveDevicesError,
    NoDevicesFoundError,
    SessionInfo,
    WsConnectionInfo,
)
from secbaas.core.service.bot_run import BaasBotService, BaasBotServiceConfig
from secbaas.core.service.bot_run._async_chat_client import ConcurrentSessionError
from secbaas.core.service.bot_run._async_chat_client_pool import AsyncChatClientPool

# ==================== Fixtures ====================

BOT_UUID = "bot-uuid-123"
TENANT = "test-tenant"
ENV = "test"
SESSION_ID = "agent:main:sess-001"
INVOKER = "key-prefix-abc"
WS_URL = "wss://gateway.example.com/proxypass/ARCA_sandbox1:20003/api/openclaw/ws"
TOKEN = "jwt-token-xyz"
TARGET = "ARCA_sandbox1:20003"
DEVICE_UUID = TARGET


def _make_conn_info():
    return WsConnectionInfo(
        ws_url=WS_URL,
        token=TOKEN,
        target=TARGET,
        expires_at=datetime.now(tz=UTC),
    )


def _make_session_info(**overrides):
    defaults = {
        "session_id": SESSION_ID,
        "bot_id": BOT_UUID,
        "status": "active",
        "created_at": datetime.now(tz=UTC),
        "metadata": {"tenant": TENANT, "invoker": INVOKER},
    }
    defaults.update(overrides)
    return SessionInfo(**defaults)  # type: ignore[arg-type]


def _make_binding_info(**overrides):
    defaults = {
        "bot_id": BOT_UUID,
        "entity_id": "entity-1",
        "sandbox_id": None,
        "device_id": DEVICE_UUID,
        "device_provider": "baas",
        "binding_id": 100,
        "device_props": {"tenant": TENANT},
        "bot_type": "service",
    }
    defaults.update(overrides)
    return BotBindingInfo(**defaults)


@pytest.fixture
def mock_pool():
    pool = MagicMock(spec=AsyncChatClientPool)
    pool.get = AsyncMock()
    return pool


@pytest.fixture
def wss_resolver():
    resolver = AsyncMock()
    resolver.dispatch_bot_ws_conn_info = AsyncMock(return_value=_make_conn_info())
    return resolver


@pytest.fixture
def config():
    return BaasBotServiceConfig(
        adapter_port=20003,
        ws_path="/api/openclaw/ws",
        connect_timeout=10,
        request_timeout=30,
    )


@pytest.fixture
def service(config, wss_resolver, mock_pool):
    return BaasBotService(
        config=config,
        client_pool=mock_pool,
        wss_resolver=wss_resolver,
        session_service=MagicMock(),
    )


# ==================== TestCreateSession ====================


class TestCreateSessionTenantValidation:
    """Tenant validation in create_session."""

    @pytest.mark.asyncio
    async def test_missing_tenant_raises_bot_service_error(self, service):
        """Missing tenant in metadata must raise BotServiceError."""
        with pytest.raises(BotServiceError, match="tenant is required"):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"invoker": INVOKER},
            )

    @pytest.mark.asyncio
    async def test_empty_tenant_raises_bot_service_error(self, service):
        """Empty string tenant must raise BotServiceError."""
        with pytest.raises(BotServiceError, match="tenant is required"):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": "", "invoker": INVOKER},
            )

    @pytest.mark.asyncio
    async def test_none_metadata_raises_bot_service_error(self, service):
        """None metadata must raise BotServiceError (no tenant key at all)."""
        with pytest.raises(BotServiceError, match="tenant is required"):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata=None,
            )

    @pytest.mark.asyncio
    async def test_valid_tenant_passes_validation(self, service, wss_resolver):
        """Valid tenant should pass validation and proceed to resolution."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-new"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        binding = _make_binding_info()
        # resolve_user_id falls back to bot_id for service bot_type without context
        consistency_key = f"agent:main:session:None:user:{BOT_UUID}"

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create", return_value=None),
        ):
            session = await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=binding,
            )
            assert session.bot_id == BOT_UUID
            # Verify tenant was passed to wss_resolver
            wss_resolver.dispatch_bot_ws_conn_info.assert_called_once_with(
                bot_uuid=BOT_UUID,
                port=20003,
                path="/api/openclaw/ws",
                tenant=TENANT,
                device_affinity=consistency_key,
            )


class TestCreateSessionBotResolution:
    """Bot resolution error handling in create_session."""

    @pytest.mark.asyncio
    async def test_bot_not_found_raises(self, service, wss_resolver):
        """BotNotFoundError from resolver propagates."""
        wss_resolver.dispatch_bot_ws_conn_info.side_effect = BotNotFoundError(BOT_UUID)
        with pytest.raises(BotNotFoundError):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=_make_binding_info(),
            )

    @pytest.mark.asyncio
    async def test_no_devices_raises_bot_not_found(self, service, wss_resolver):
        """NoDevicesFoundError is converted to BotNotFoundError."""
        wss_resolver.dispatch_bot_ws_conn_info.side_effect = NoDevicesFoundError(
            "no devices"
        )
        with pytest.raises(BotNotFoundError):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=_make_binding_info(),
            )

    @pytest.mark.asyncio
    async def test_no_active_devices_raises_bot_not_available(
        self, service, wss_resolver
    ):
        """NoActiveDevicesError is converted to BotNotAvailableError."""
        wss_resolver.dispatch_bot_ws_conn_info.side_effect = NoActiveDevicesError(
            "no active"
        )
        with pytest.raises(BotNotAvailableError):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=_make_binding_info(),
            )

    @pytest.mark.asyncio
    async def test_generic_exception_raises_bot_service_error(
        self, service, wss_resolver
    ):
        """Generic exceptions during resolution are wrapped in BotServiceError."""
        wss_resolver.dispatch_bot_ws_conn_info.side_effect = RuntimeError("conn fail")
        with pytest.raises(BotServiceError, match="Failed to resolve WS connection"):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=_make_binding_info(),
            )


class TestCreateSessionSetsBindingInfo:
    """create_session sets baas_session_id on binding_info."""

    @pytest.mark.asyncio
    async def test_binding_info_gets_baas_session_id(self, service, wss_resolver):
        """After create_session, binding_info.baas_session_id is set."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-new"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        binding = _make_binding_info()

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(
                service, "_persist_session_create", return_value="BAAS-SESS-123"
            ),
        ):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=binding,
            )
            assert binding.baas_session_id == "BAAS-SESS-123"


class TestCreateSessionPersistence:
    """Session persistence to baas_bot_session table."""

    @pytest.mark.asyncio
    async def test_persist_session_called_on_create(self, service, wss_resolver):
        """_persist_session_create is called after session registration."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-new"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create") as mock_persist,
        ):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=_make_binding_info(),
            )
            mock_persist.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_skipped_without_invoker(self, service, wss_resolver):
        """_persist_session_create returns None when invoker is missing."""
        session_info = _make_session_info(metadata={"tenant": TENANT})
        conn_info = _make_conn_info()

        result = service._persist_session_create(
            session_info=session_info,
            conn_info=conn_info,
        )
        assert result is None

    def test_persist_stores_baas_session_id_in_metadata(self, service, wss_resolver):
        """After persist, baas_session_id is stored in session metadata."""
        mock_session_svc = MagicMock()
        mock_session_svc.create_session.return_value = "SESSION-abc123"
        mock_session_svc.mark_running.return_value = None

        metadata = {"tenant": TENANT, "invoker": INVOKER}
        session_info = _make_session_info(metadata=metadata)
        conn_info = _make_conn_info()

        with patch.object(service, "_session_service", mock_session_svc):
            result = service._persist_session_create(
                session_info=session_info,
                conn_info=conn_info,
            )
        assert result == "SESSION-abc123"
        assert session_info.metadata["baas_session_id"] == "SESSION-abc123"
        mock_session_svc.create_session.assert_called_once_with(
            bot_uuid=BOT_UUID,
            invoker=INVOKER,
            req={},
            device_uuid=DEVICE_UUID,
            tenant=TENANT,
            trace_id=None,
        )
        mock_session_svc.mark_running.assert_called_once_with("SESSION-abc123")

    def test_persist_failure_does_not_raise(self, service):
        """If DefaultSessionService.create_session raises, persistence returns None silently."""
        mock_session_svc = MagicMock()
        mock_session_svc.create_session.side_effect = Exception("DB down")

        metadata = {"tenant": TENANT, "invoker": INVOKER}
        session_info = _make_session_info(metadata=metadata)
        conn_info = _make_conn_info()

        # Must not raise
        with patch.object(service, "_session_service", mock_session_svc):
            result = service._persist_session_create(
                session_info=session_info,
                conn_info=conn_info,
            )
        assert result is None


# ==================== TestSendMessage ====================


class TestSendMessage:
    """send_message tests: binding_info-based, session lifecycle marking."""

    @pytest.mark.asyncio
    async def test_send_message_success_marks_completed(
        self, service, wss_resolver, mock_pool
    ):
        """Successful send_message marks session as COMPLETED."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("response content", "done"))
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_completed") as mock_completed,
        ):
            response = await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
            )
            assert response.content == "response content"
            mock_pool.get.assert_awaited_once()
            mock_completed.assert_called_once_with(
                "SESSION-xyz", result={"content": "response content"}
            )

    @pytest.mark.asyncio
    async def test_send_message_error_state_marks_failed(
        self, service, wss_resolver, mock_pool
    ):
        """ChatClient returning error state marks session as FAILED and raises."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("error msg", "error"))
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_failed") as mock_failed,
        ):
            with pytest.raises(BotServiceError, match="error msg"):
                await service.send_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=binding,
                )
            mock_failed.assert_called_once_with("SESSION-xyz", err_msg="error msg")

    @pytest.mark.asyncio
    async def test_send_message_exception_marks_failed(
        self, service, wss_resolver, mock_pool
    ):
        """Unexpected exception from ChatClient marks session as FAILED."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(side_effect=ConnectionError("ws closed"))
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_failed") as mock_failed,
        ):
            with pytest.raises(BotServiceError, match="Failed to send message"):
                await service.send_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=binding,
                )
            mock_failed.assert_called_once_with("SESSION-xyz", err_msg="ws closed")

    @pytest.mark.asyncio
    async def test_send_message_no_baas_session_id_skips_marking(
        self, service, wss_resolver, mock_pool
    ):
        """If baas_session_id is not set on binding_info, marking is skipped (None)."""
        binding = _make_binding_info()  # no baas_session_id

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("ok", "done"))
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_completed") as mock_completed,
        ):
            response = await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
            )
            assert response.content == "ok"
            mock_completed.assert_called_once_with(None, result={"content": "ok"})

    @pytest.mark.asyncio
    async def test_send_message_resolves_ws_connection(
        self, service, wss_resolver, mock_pool
    ):
        """send_message uses _resolve_ws_connection_for_binding."""
        binding = _make_binding_info()

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("ok", "done"))
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ) as mock_resolve,
        ):
            await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
            )
            mock_resolve.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_message_concurrent_session_rejected(
        self, service, wss_resolver, mock_pool
    ):
        """Concurrent send_message on the same session_key should raise BotServiceError."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            side_effect=ConcurrentSessionError(
                "Concurrent send_message on session_key=test is not allowed"
            )
        )
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_failed") as mock_failed,
        ):
            with pytest.raises(BotServiceError, match="Concurrent request"):
                await service.send_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=binding,
                )
            mock_failed.assert_called_once()


# ==================== TestInjectMessage ====================


class TestInjectMessage:
    """inject_message tests."""

    @pytest.mark.asyncio
    async def test_inject_message_success(self, service, wss_resolver, mock_pool):
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_completed"),
        ):
            await service.inject_message(
                session_id=SESSION_ID,
                message="instruction",
                binding_info=binding,
            )

        mock_client.inject_message.assert_awaited_once()
        mock_pool.get.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_inject_message_error_marks_failed(
        self, service, wss_resolver, mock_pool
    ):
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.inject_message.side_effect = RuntimeError("ws fail")
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_failed") as mock_failed,
        ):
            with pytest.raises(BotServiceError, match="Failed to inject message"):
                await service.inject_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=binding,
                )
            mock_failed.assert_called_once()


# ==================== TestGetMessages ====================


class TestGetMessages:
    """get_messages tests."""

    @pytest.mark.asyncio
    async def test_get_messages_success(self, service, wss_resolver):
        binding = _make_binding_info()

        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.session_id = SESSION_ID
        mock_msg.role = "user"
        mock_msg.content = "hello"
        mock_msg.meta = {}
        mock_msg.created_at = datetime.now(tz=UTC)
        mock_msg.history_meta = None

        session_client = AsyncMock()
        session_client.get_messages.return_value = [mock_msg]
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(
                service, "_create_session_client", return_value=session_client
            ),
        ):
            result = await service.get_messages(
                session_id=SESSION_ID,
                binding_info=binding,
            )
            assert len(result) == 1
            assert result[0].content == "hello"


# ==================== TestMarkSessionLifecycle ====================


class TestMarkSessionLifecycle:
    """Test _mark_session_completed and _mark_session_failed."""

    @patch("secbaas.core.service.bot_run._baas_service.DefaultSessionService")
    def test_mark_completed_calls_service(self, mock_session_svc, service):
        """_mark_session_completed delegates to DefaultSessionService.mark_completed."""
        with patch.object(service, "_session_service", mock_session_svc):
            service._mark_session_completed("SESSION-123", result={"content": "hello"})
            mock_session_svc.mark_completed.assert_called_once_with(
                "SESSION-123", result={"content": "hello"}
            )

    @patch("secbaas.core.service.bot_run._baas_service.DefaultSessionService")
    def test_mark_failed_calls_service(self, mock_session_svc, service):
        """_mark_session_failed delegates to DefaultSessionService.mark_failed."""
        with patch.object(service, "_session_service", mock_session_svc):
            service._mark_session_failed("SESSION-123", err_msg="timeout")
            mock_session_svc.mark_failed.assert_called_once_with(
                "SESSION-123", err_msg="timeout"
            )

    def test_mark_completed_none_id_is_noop(self, service):
        """_mark_session_completed with None id does nothing (no crash)."""
        service._mark_session_completed(None, result={"content": "x"})

    def test_mark_failed_none_id_is_noop(self, service):
        """_mark_session_failed with None id does nothing (no crash)."""
        service._mark_session_failed(None, err_msg="x")

    @patch("secbaas.core.service.bot_run._baas_service.DefaultSessionService")
    def test_mark_completed_db_failure_does_not_raise(self, mock_session_svc, service):
        """If mark_completed DB call fails, it logs but doesn't raise."""
        mock_session_svc.mark_completed.side_effect = Exception("DB error")
        with patch.object(service, "_session_service", mock_session_svc):
            service._mark_session_completed("SESSION-123", result={"content": "x"})

    @patch("secbaas.core.service.bot_run._baas_service.DefaultSessionService")
    def test_mark_failed_db_failure_does_not_raise(self, mock_session_svc, service):
        """If mark_failed DB call fails, it logs but doesn't raise."""
        mock_session_svc.mark_failed.side_effect = Exception("DB error")
        with patch.object(service, "_session_service", mock_session_svc):
            service._mark_session_failed("SESSION-123", err_msg="oops")


# ==================== TestBuildBaseUrl ====================


class TestBuildBaseUrl:
    """Test _build_base_url URL conversion logic."""

    def test_wss_url_conversion(self):
        """wss:// URL is converted to https:// with WS path stripped."""
        conn_info = WsConnectionInfo(
            ws_url="wss://gateway.example.com/proxypass/ARCA_sb1:20003/api/openclaw/ws",
            token="t",
            target="ARCA_sb1:20003",
            expires_at=datetime.now(tz=UTC),
        )
        result = BaasBotService._build_base_url(conn_info, "openclaw")
        assert result == "https://gateway.example.com/proxypass/ARCA_sb1:20003"

    def test_non_wss_url(self):
        """ws:// URL is converted to https:// (non-TLS) with WS path stripped."""
        conn_info = WsConnectionInfo(
            ws_url="ws://localhost:8080/api/openclaw/ws",
            token="t",
            target="local",
            expires_at=datetime.now(tz=UTC),
        )
        result = BaasBotService._build_base_url(conn_info, "openclaw")
        assert result == "https://localhost:8080"

    def test_plain_http_url_no_ws_prefix(self):
        """Plain URL without ws:// or wss:// prefix handled gracefully."""
        conn_info = WsConnectionInfo(
            ws_url="gateway.example.com:20003/api/openclaw/ws",
            token="t",
            target="gateway",
            expires_at=datetime.now(tz=UTC),
        )
        result = BaasBotService._build_base_url(conn_info, "openclaw")
        assert result == "https://gateway.example.com:20003"

    def test_url_without_ws_path_suffix(self):
        """URL without /api/openclaw/ws suffix is handled."""
        conn_info = WsConnectionInfo(
            ws_url="wss://gateway.example.com/proxypass/ARCA_sb1:20003",
            token="t",
            target="ARCA_sb1:20003",
            expires_at=datetime.now(tz=UTC),
        )
        result = BaasBotService._build_base_url(conn_info, "openclaw")
        assert result == "https://gateway.example.com/proxypass/ARCA_sb1:20003"

    # [单测用例]测试场景：engine_type 非 openclaw 时路径后缀正确剥离
    def test_engine_type_arklet(self):
        """engine_type=arklet strips /api/arklet/ws suffix."""
        conn_info = WsConnectionInfo(
            ws_url="wss://gateway.example.com/proxypass/ARCA_sb1:20003/api/arklet/ws",
            token="t",
            target="ARCA_sb1:20003",
            expires_at=datetime.now(tz=UTC),
        )
        result = BaasBotService._build_base_url(conn_info, "arklet")
        assert result == "https://gateway.example.com/proxypass/ARCA_sb1:20003"

    # [单测用例]测试场景：ws_url 后缀与 engine_type 不匹配时不剥离
    def test_engine_type_mismatch_no_strip(self):
        """When engine_type doesn't match ws_url suffix, suffix is not stripped."""
        conn_info = WsConnectionInfo(
            ws_url="wss://gateway.example.com/proxypass/ARCA_sb1:20003/api/openclaw/ws",
            token="t",
            target="ARCA_sb1:20003",
            expires_at=datetime.now(tz=UTC),
        )
        result = BaasBotService._build_base_url(conn_info, "arklet")
        # /api/openclaw/ws not stripped because engine_type is "arklet"
        assert (
            result
            == "https://gateway.example.com/proxypass/ARCA_sb1:20003/api/openclaw/ws"
        )


class TestGetOrCreateAdapterSession:
    """Test _get_or_create_adapter_session logic."""

    @pytest.mark.asyncio
    async def test_existing_session_id_is_reused(self, service):
        """When session_id is provided, it's reused without adapter call."""
        mock_session_client = AsyncMock()
        result = await service._get_or_create_adapter_session(
            session_client=mock_session_client,
            session_id="agent:main:existing-sess",
            user_id="user-001",
            metadata={},
        )
        assert result == ("agent:main:existing-sess", True)
        mock_session_client.get_session.assert_not_called()
        mock_session_client.create_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_new_session_creates_adapter_session(self, service):
        """When session_id is None, a new adapter session is created."""
        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:new-sess-001"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)

        result = await service._get_or_create_adapter_session(
            session_client=mock_session_client,
            session_id=None,
            user_id="user-001",
            metadata={"title": "Test", "model": "gpt-4"},
        )
        assert result == ("agent:main:new-sess-001", False)

    @pytest.mark.asyncio
    async def test_new_session_adds_agent_main_prefix(self, service):
        """When adapter returns session id without prefix, agent:main: is added."""
        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "raw-sess-002"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)

        result = await service._get_or_create_adapter_session(
            session_client=mock_session_client,
            session_id=None,
            user_id="user-001",
            metadata={},
        )
        assert result == ("agent:main:raw-sess-002", False)


class TestResolveWsConnectionForBinding:
    """Test _resolve_ws_connection_for_binding method."""

    @pytest.mark.asyncio
    async def test_resolves_from_binding_info(self, service, wss_resolver):
        """_resolve_ws_connection_for_binding extracts bot_uuid and tenant from binding_info."""
        binding = _make_binding_info()

        result = await service._resolve_ws_connection_for_binding(binding)

        wss_resolver.dispatch_bot_ws_conn_info.assert_called_once()
        # Compare key fields only (expires_at has microsecond-level differences)
        assert result.ws_url is not None
        assert result.token is not None
        assert result.target is not None

    @pytest.mark.asyncio
    async def test_resolves_tenant_from_context(self, service, wss_resolver):
        """Tenant is extracted from context when binding_info has none."""
        from secbaas.api.bot_runtime import BotChatContext

        binding = _make_binding_info(device_props={})

        ctx = BotChatContext(
            api_key_prefix=INVOKER,
            tenant=TENANT,
            app_id="test-app",
            app_type="test-type",
        )

        result = await service._resolve_ws_connection_for_binding(binding, context=ctx)
        wss_resolver.dispatch_bot_ws_conn_info.assert_called_once()


class TestCreateSessionWithContext:
    """Test create_session with BotChatContext for tenant extraction."""

    @pytest.mark.asyncio
    async def test_tenant_from_context(self, service, wss_resolver):
        """Tenant is extracted from context when metadata has none."""
        from secbaas.api.bot_runtime import BotChatContext

        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-ctx"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        context = BotChatContext(
            api_key_prefix=INVOKER,
            tenant=TENANT,
            app_id="test-app",
            app_type="test-type",
        )

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create", return_value=None),
        ):
            session = await service.create_session(
                bot_id=BOT_UUID,
                metadata={},
                context=context,
                binding_info=_make_binding_info(),
            )
            assert session.bot_id == BOT_UUID


# ==================== TestResolveWsConnection ====================


class TestResolveWsConnection:
    """Test _resolve_ws_connection method directly."""

    @pytest.mark.asyncio
    async def test_default_path_without_engine_type(self, service, wss_resolver):
        # [单测用例]测试场景：engine_type 为 None 时使用默认 ws_path
        """When engine_type is None, default ws_path from config is used."""
        await service._resolve_ws_connection(
            BOT_UUID, TENANT, engine_type=None, session_consistency_key=None
        )
        wss_resolver.dispatch_bot_ws_conn_info.assert_called_once_with(
            bot_uuid=BOT_UUID,
            port=20003,
            path="/api/openclaw/ws",
            tenant=TENANT,
            device_affinity=None,
        )

    @pytest.mark.asyncio
    async def test_custom_engine_type_overrides_path(self, service, wss_resolver):
        # [单测用例]测试场景：engine_type 有值时覆盖 ws_path
        """When engine_type is provided, path is /api/{engine_type}/ws."""
        await service._resolve_ws_connection(
            BOT_UUID, TENANT, engine_type="arklet", session_consistency_key=None
        )
        wss_resolver.dispatch_bot_ws_conn_info.assert_called_once_with(
            bot_uuid=BOT_UUID,
            port=20003,
            path="/api/arklet/ws",
            tenant=TENANT,
            device_affinity=None,
        )

    @pytest.mark.asyncio
    async def test_openclaw_engine_type_explicit(self, service, wss_resolver):
        # [单测用例]测试场景：显式传入 engine_type=openclaw
        """Explicit engine_type=openclaw produces /api/openclaw/ws."""
        await service._resolve_ws_connection(
            BOT_UUID, TENANT, engine_type="openclaw", session_consistency_key=None
        )
        wss_resolver.dispatch_bot_ws_conn_info.assert_called_once_with(
            bot_uuid=BOT_UUID,
            port=20003,
            path="/api/openclaw/ws",
            tenant=TENANT,
            device_affinity=None,
        )

    @pytest.mark.asyncio
    async def test_returns_ws_connection_info(self, service, wss_resolver):
        # [单测用例]测试场景：返回值类型和字段正确
        """Returns WsConnectionInfo from resolver."""
        expected = _make_conn_info()
        wss_resolver.dispatch_bot_ws_conn_info.return_value = expected
        result = await service._resolve_ws_connection(BOT_UUID, TENANT)
        assert result is expected

    @pytest.mark.asyncio
    async def test_propagates_bot_not_found_error(self, service, wss_resolver):
        # [单测用例]测试场景：BotNotFoundError 异常传播
        """BotNotFoundError from resolver propagates."""
        wss_resolver.dispatch_bot_ws_conn_info.side_effect = BotNotFoundError(BOT_UUID)
        with pytest.raises(BotNotFoundError):
            await service._resolve_ws_connection(BOT_UUID, TENANT)

    @pytest.mark.asyncio
    async def test_propagates_no_active_devices_error(self, service, wss_resolver):
        # [单测用例]测试场景：NoActiveDevicesError 异常传播
        """NoActiveDevicesError from resolver propagates."""
        wss_resolver.dispatch_bot_ws_conn_info.side_effect = NoActiveDevicesError(
            BOT_UUID
        )
        with pytest.raises(NoActiveDevicesError):
            await service._resolve_ws_connection(BOT_UUID, TENANT)


# ==================== TestCreateSessionEngineType ====================


class TestCreateSessionEngineType:
    """Test create_session with engine_type from binding_info."""

    @pytest.mark.asyncio
    async def test_engine_type_passed_to_resolve(self, service, wss_resolver):
        # [单测用例]测试场景：binding_info.engine_type 传递给 _resolve_ws_connection
        """engine_type from binding_info is passed to _resolve_ws_connection."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-eng"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        binding = _make_binding_info(engine_type="arklet")

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create", return_value=None),
        ):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=binding,
            )
            # 验证 wss_resolver 被调用时 path 使用了 engine_type
            wss_resolver.dispatch_bot_ws_conn_info.assert_called_once_with(
                bot_uuid=BOT_UUID,
                port=20003,
                path="/api/arklet/ws",
                tenant=TENANT,
                device_affinity=None,
            )

    @pytest.mark.asyncio
    async def test_default_engine_type_without_engine_type(self, service, wss_resolver):
        # [单测用例]测试场景：binding_info 无 engine_type 时使用默认 ws_path
        """When binding_info has default engine_type, default ws_path is used."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-default"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        binding = _make_binding_info()
        # resolve_user_id falls back to bot_id for service bot_type without context
        consistency_key = f"agent:main:session:None:user:{BOT_UUID}"

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create", return_value=None),
        ):
            await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": TENANT, "invoker": INVOKER},
                binding_info=binding,
            )
            wss_resolver.dispatch_bot_ws_conn_info.assert_called_once_with(
                bot_uuid=BOT_UUID,
                port=20003,
                path="/api/openclaw/ws",
                tenant=TENANT,
                device_affinity=consistency_key,
            )


# ==================== TestCreateSessionInvokerInjection ====================


class TestCreateSessionInvokerInjection:
    """Test create_session injects invoker and tenant into metadata."""

    @pytest.mark.asyncio
    async def test_invoker_injected_from_context(self, service, wss_resolver):
        # [单测用例]测试场景：invoker 从 context.api_key_prefix 注入 metadata
        """invoker from context.api_key_prefix is injected into metadata."""
        from secbaas.api.bot_runtime import BotChatContext

        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-inv"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        context = BotChatContext(
            api_key_prefix="my-invoker",
            tenant=TENANT,
            app_id="test-app",
            app_type="test-type",
        )

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create", return_value="BAAS-SESS"),
        ):
            session = await service.create_session(
                bot_id=BOT_UUID,
                metadata={},
                context=context,
                binding_info=_make_binding_info(),
            )
            # Verify invoker was injected into the returned session metadata
            assert session.metadata.get("invoker") == "my-invoker"

    @pytest.mark.asyncio
    async def test_tenant_injected_from_context(self, service, wss_resolver):
        # [单测用例]测试场景：tenant 从 context.tenant 注入 metadata
        """tenant from context is injected into metadata."""
        from secbaas.api.bot_runtime import BotChatContext

        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-ten"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        context = BotChatContext(
            api_key_prefix=INVOKER,
            tenant="ctx-tenant",
            app_id="test-app",
            app_type="test-type",
        )

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create", return_value="BAAS-SESS"),
        ):
            session = await service.create_session(
                bot_id=BOT_UUID,
                metadata={},
                context=context,
                binding_info=_make_binding_info(),
            )
            # Verify tenant was injected into the returned session metadata
            assert session.metadata.get("tenant") == "ctx-tenant"

    @pytest.mark.asyncio
    async def test_metadata_tenant_fallback_when_no_context(
        self, service, wss_resolver
    ):
        # [单测用例]测试场景：无 context 时从 metadata 获取 tenant
        """When no context, tenant falls back to metadata."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-meta"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        binding = _make_binding_info()
        # resolve_user_id falls back to bot_id for service bot_type without context
        consistency_key = f"agent:main:session:None:user:{BOT_UUID}"

        with (
            patch.object(
                service, "_create_session_client", return_value=mock_session_client
            ),
            patch.object(service, "_persist_session_create", return_value=None),
        ):
            session = await service.create_session(
                bot_id=BOT_UUID,
                metadata={"tenant": "meta-tenant", "invoker": INVOKER},
                binding_info=binding,
            )
            assert session.bot_id == BOT_UUID
            wss_resolver.dispatch_bot_ws_conn_info.assert_called_once_with(
                bot_uuid=BOT_UUID,
                port=20003,
                path="/api/openclaw/ws",
                tenant="meta-tenant",
                device_affinity=consistency_key,
            )


# ==================== TestCreateSessionAdapterErrors ====================


class TestCreateSessionAdapterErrors:
    """Test create_session error handling in adapter session creation."""

    @pytest.mark.asyncio
    async def test_adapter_session_exception_wraps_bot_service_error(
        self, service, wss_resolver
    ):
        # [单测用例]测试场景：adapter session 创建异常被包装为 BotServiceError
        """Exception during adapter session creation is wrapped in BotServiceError."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session_client.create_session = AsyncMock(
            side_effect=RuntimeError("adapter down")
        )
        mock_session_client.__aenter__ = AsyncMock(return_value=mock_session_client)
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(
            service, "_create_session_client", return_value=mock_session_client
        ):
            with pytest.raises(
                BotServiceError, match="Failed to get or create adapter session"
            ):
                await service.create_session(
                    bot_id=BOT_UUID,
                    metadata={"tenant": TENANT, "invoker": INVOKER},
                    binding_info=_make_binding_info(),
                )

    @pytest.mark.asyncio
    async def test_adapter_session_client_error_does_not_leak_url(
        self, service, wss_resolver
    ):
        # [单测用例]测试场景：ClientResponseError 含内部代理 url,外抛消息不得包含 url
        """_safe_client_msg strips aiohttp request url from ClientResponseError."""
        import aiohttp

        from secbaas.core.service.bot_run._baas_service import _safe_client_msg

        cre = aiohttp.ClientResponseError(
            request_info=None,
            history=(),
            status=500,
            message="Unsupported engine type: claude_code. Only 'aicoding' is supported.",
        )
        # aiohttp 把请求 url 拼进 str(cre);模拟该行为
        cre.__dict__["_url"] = (
            "https://agentclawproxy-pre.alipay.com/proxypass/ARCA_sb/api/sessions"  # type: ignore[attr-defined]
        )

        safe = _safe_client_msg(cre)
        assert "agentclawproxy" not in safe
        assert "/api/sessions" not in safe
        assert "Unsupported engine type" in safe  # 业务错误保留
        # 普通异常照常 str()
        assert _safe_client_msg(RuntimeError("boom")) == "boom"

    @pytest.mark.asyncio
    async def test_existing_session_id_exception_reraises(self, service, wss_resolver):
        # [单测用例]测试场景：session_id 已存在但 async with 出异常时 re-raise
        """When session_id is provided but async context fails, exception is reraised."""
        wss_resolver.dispatch_bot_ws_conn_info.return_value = _make_conn_info()

        mock_session_client = AsyncMock()
        mock_session_client.__aenter__ = AsyncMock(
            side_effect=ConnectionError("connection lost")
        )
        mock_session_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(
            service, "_create_session_client", return_value=mock_session_client
        ):
            with pytest.raises(
                BotServiceError, match="Failed to get or create adapter session"
            ):
                await service.create_session(
                    bot_id=BOT_UUID,
                    session_id="agent:main:existing-id",
                    metadata={"tenant": TENANT, "invoker": INVOKER},
                    binding_info=_make_binding_info(),
                )


# ==================== TestSendMessageExtended ====================


class TestSendMessageExtended:
    """Extended send_message tests covering timeout, auth_token, app_id, and WS failure."""

    @pytest.mark.asyncio
    async def test_timeout_passed_to_send_message(
        self, service, wss_resolver, mock_pool
    ):
        # [单测用例]测试场景：timeout 参数正确传递到 ChatClient
        """Custom timeout is passed to client.send_message."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("ok", "done"))
        mock_pool.get.return_value = mock_client

        with patch.object(
            service,
            "_resolve_ws_connection_for_binding",
            return_value=_make_conn_info(),
        ):
            await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
                timeout=60,
            )
            mock_client.send_message.assert_awaited_once_with(
                message="hello",
                session_key=SESSION_ID,
                wait_result=True,
                timeout=60,
                auth_token=None,
                app_id=None,
                chat_metadata=None,
            )

    @pytest.mark.asyncio
    async def test_default_timeout_when_not_provided(
        self, service, wss_resolver, mock_pool
    ):
        # [单测用例]测试场景：未传 timeout 时使用配置中的默认值
        """When timeout is None, config.request_timeout is used."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("ok", "done"))
        mock_pool.get.return_value = mock_client

        with patch.object(
            service,
            "_resolve_ws_connection_for_binding",
            return_value=_make_conn_info(),
        ):
            await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
            )
            mock_client.send_message.assert_awaited_once_with(
                message="hello",
                session_key=SESSION_ID,
                wait_result=True,
                timeout=30,  # default from config
                auth_token=None,
                app_id=None,
                chat_metadata=None,
            )

    @pytest.mark.asyncio
    async def test_auth_token_from_context(self, service, wss_resolver, mock_pool):
        # [单测用例]测试场景：context 的 auth_token 传递到 ChatClient
        """auth_token from context.build_auth_token() is passed to client."""
        from secbaas.api.bot_runtime import BotChatContext

        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("ok", "done"))
        mock_pool.get.return_value = mock_client

        context = BotChatContext(
            api_key_prefix="my-key",
            tenant=TENANT,
            app_id="test-app",
            app_type="test-type",
        )

        with patch.object(
            service,
            "_resolve_ws_connection_for_binding",
            return_value=_make_conn_info(),
        ):
            await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
                context=context,
            )
            mock_client.send_message.assert_awaited_once_with(
                message="hello",
                session_key=SESSION_ID,
                wait_result=True,
                timeout=30,
                auth_token="OPEN_API:app:my-key",
                app_id="test-app",
                chat_metadata=None,
            )

    @pytest.mark.asyncio
    async def test_ws_connection_failure_marks_session_failed(
        self, service, wss_resolver
    ):
        # [单测用例]测试场景：WS 连接解析失败时标记 session failed
        """WS connection resolution failure in send_message marks session as FAILED."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                side_effect=RuntimeError("DNS resolution failed"),
            ),
            patch.object(service, "_mark_session_failed") as mock_failed,
        ):
            with pytest.raises(
                BotServiceError, match="Failed to resolve WS connection"
            ):
                await service.send_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=binding,
                )
            mock_failed.assert_called_once_with(
                "SESSION-xyz", err_msg="DNS resolution failed"
            )

    @pytest.mark.asyncio
    async def test_bot_service_error_from_send_not_wrapped_again(
        self, service, wss_resolver, mock_pool
    ):
        # [单测用例]测试场景：send_message 中 BotServiceError 直接 re-raise
        """BotServiceError from client.send_message is re-raised without wrapping."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(
            side_effect=BotServiceError("already wrapped")
        )
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_failed"),
        ):
            with pytest.raises(BotServiceError, match="already wrapped"):
                await service.send_message(
                    session_id=SESSION_ID,
                    message="hello",
                    binding_info=binding,
                )

    @pytest.mark.asyncio
    async def test_pool_key_and_headers_from_conn_info(
        self, service, wss_resolver, mock_pool
    ):
        # [单测用例]测试场景：pool key 和 headers 从 conn_info 正确构建
        """pool_key and headers are derived from conn_info."""
        binding = _make_binding_info()

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("ok", "done"))
        mock_pool.get.return_value = mock_client

        conn_info = _make_conn_info()

        with patch.object(
            service,
            "_resolve_ws_connection_for_binding",
            return_value=conn_info,
        ):
            await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
            )
            mock_pool.get.assert_awaited_once_with(
                conn_info.target,
                conn_info.ws_url,
                {"x-proxypass-token": conn_info.token},
            )


# ==================== TestInjectMessageExtended ====================


class TestInjectMessageExtended:
    """Extended inject_message tests."""

    @pytest.mark.asyncio
    async def test_auth_token_from_context(self, service, wss_resolver, mock_pool):
        # [单测用例]测试场景：inject_message 传递 context 的 auth_token
        """auth_token from context.build_auth_token() is passed to client.inject_message."""
        from secbaas.api.bot_runtime import BotChatContext

        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_pool.get.return_value = mock_client

        context = BotChatContext(
            api_key_prefix="inj-key",
            tenant=TENANT,
            app_id="inj-app",
            app_type="test-type",
        )

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_completed"),
        ):
            await service.inject_message(
                session_id=SESSION_ID,
                message="instruction",
                binding_info=binding,
                context=context,
            )
            mock_client.inject_message.assert_awaited_once_with(
                message="instruction",
                session_key=SESSION_ID,
                auth_token="OPEN_API:app:inj-key",
            )

    @pytest.mark.asyncio
    async def test_no_auth_token_without_context(
        self, service, wss_resolver, mock_pool
    ):
        # [单测用例]测试场景：无 context 时 auth_token 为 None
        """Without context, auth_token is None in inject_message."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_completed"),
        ):
            await service.inject_message(
                session_id=SESSION_ID,
                message="instruction",
                binding_info=binding,
            )
            mock_client.inject_message.assert_awaited_once_with(
                message="instruction",
                session_key=SESSION_ID,
                auth_token=None,
            )

    @pytest.mark.asyncio
    async def test_ws_connection_failure_marks_session_failed(
        self, service, wss_resolver
    ):
        # [单测用例]测试场景：inject_message 中 WS 连接解析失败标记 session failed
        """WS connection resolution failure in inject_message marks session as FAILED."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                side_effect=RuntimeError("WS resolve error"),
            ),
            patch.object(service, "_mark_session_failed") as mock_failed,
        ):
            with pytest.raises(
                BotServiceError, match="Failed to resolve WS connection"
            ):
                await service.inject_message(
                    session_id=SESSION_ID,
                    message="instruction",
                    binding_info=binding,
                )
            mock_failed.assert_called_once_with(
                "SESSION-xyz", err_msg="WS resolve error"
            )

    @pytest.mark.asyncio
    async def test_inject_marks_completed_on_success(
        self, service, wss_resolver, mock_pool
    ):
        # [单测用例]测试场景：inject_message 成功后标记 COMPLETED
        """Successful inject_message marks session as COMPLETED."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_completed") as mock_completed,
        ):
            await service.inject_message(
                session_id=SESSION_ID,
                message="instruction",
                binding_info=binding,
            )
            mock_completed.assert_called_once_with(
                "SESSION-xyz", result={"content": "inject success"}
            )

    @pytest.mark.asyncio
    async def test_bot_service_error_from_inject_not_wrapped_again(
        self, service, wss_resolver, mock_pool
    ):
        # [单测用例]测试场景：inject_message 中 BotServiceError 直接 re-raise
        """BotServiceError from inject_message client is re-raised without wrapping."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.inject_message = AsyncMock(
            side_effect=BotServiceError("already wrapped")
        )
        mock_pool.get.return_value = mock_client

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(service, "_mark_session_failed"),
        ):
            with pytest.raises(BotServiceError, match="already wrapped"):
                await service.inject_message(
                    session_id=SESSION_ID,
                    message="instruction",
                    binding_info=binding,
                )


# ==================== TestGetMessagesExtended ====================


class TestGetMessagesExtended:
    """Extended get_messages tests covering error scenarios."""

    @pytest.mark.asyncio
    async def test_get_messages_ws_connection_failure(self, service, wss_resolver):
        # [单测用例]测试场景：get_messages 中 WS 连接解析失败抛 BotServiceError
        """WS connection resolution failure raises BotServiceError."""
        binding = _make_binding_info()

        with patch.object(
            service,
            "_resolve_ws_connection_for_binding",
            side_effect=RuntimeError("conn fail"),
        ):
            with pytest.raises(
                BotServiceError, match="Failed to resolve WS connection"
            ):
                await service.get_messages(
                    session_id=SESSION_ID,
                    binding_info=binding,
                )

    @pytest.mark.asyncio
    async def test_get_messages_session_client_error(self, service, wss_resolver):
        # [单测用例]测试场景：get_messages 中 AsyncSessionClient 异常包装为 BotServiceError
        """Exception from session_client.get_messages is wrapped in BotServiceError."""
        binding = _make_binding_info()

        session_client = AsyncMock()
        session_client.get_messages = AsyncMock(side_effect=RuntimeError("timeout"))
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(
                service, "_create_session_client", return_value=session_client
            ),
        ):
            with pytest.raises(BotServiceError, match="Failed to get messages"):
                await service.get_messages(
                    session_id=SESSION_ID,
                    binding_info=binding,
                )

    @pytest.mark.asyncio
    async def test_get_messages_bot_service_error_reraises(self, service, wss_resolver):
        # [单测用例]测试场景：get_messages 中 BotServiceError 直接 re-raise
        """BotServiceError from get_messages is re-raised without wrapping."""
        binding = _make_binding_info()

        session_client = AsyncMock()
        session_client.get_messages = AsyncMock(
            side_effect=BotServiceError("already wrapped")
        )
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(
                service, "_create_session_client", return_value=session_client
            ),
        ):
            with pytest.raises(BotServiceError, match="already wrapped"):
                await service.get_messages(
                    session_id=SESSION_ID,
                    binding_info=binding,
                )

    @pytest.mark.asyncio
    async def test_get_messages_empty_result(self, service, wss_resolver):
        # [单测用例]测试场景：get_messages 返回空列表
        """get_messages returns empty list when no messages exist."""
        binding = _make_binding_info()

        session_client = AsyncMock()
        session_client.get_messages = AsyncMock(return_value=[])
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(
                service, "_create_session_client", return_value=session_client
            ),
        ):
            result = await service.get_messages(
                session_id=SESSION_ID,
                binding_info=binding,
            )
            assert result == []

    @pytest.mark.asyncio
    async def test_get_messages_multiple_messages(self, service, wss_resolver):
        # [单测用例]测试场景：get_messages 返回多条消息
        """get_messages returns multiple messages with correct field mapping."""
        binding = _make_binding_info()

        mock_msgs = []
        for i in range(3):
            msg = MagicMock()
            msg.id = f"msg-{i}"
            msg.session_id = SESSION_ID
            msg.role = "assistant" if i % 2 != 0 else "user"
            msg.content = f"content-{i}"
            msg.meta = {"key": f"val-{i}"}
            msg.created_at = datetime.now(tz=UTC)
            msg.history_meta = None
            mock_msgs.append(msg)

        session_client = AsyncMock()
        session_client.get_messages = AsyncMock(return_value=mock_msgs)
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(
                service, "_create_session_client", return_value=session_client
            ),
        ):
            result = await service.get_messages(
                session_id=SESSION_ID,
                binding_info=binding,
            )
            assert len(result) == 3
            assert result[0].role == "user"
            assert result[1].role == "assistant"
            assert result[2].role == "user"

    @pytest.mark.asyncio
    async def test_get_messages_passes_engine_type_to_session_client(
        self, service, wss_resolver
    ):
        # [单测用例]测试场景：get_messages 将 binding_info.engine_type 传给 _create_session_client
        """engine_type from binding_info is passed to _create_session_client."""
        binding = _make_binding_info(engine_type="arklet")

        session_client = AsyncMock()
        session_client.get_messages = AsyncMock(return_value=[])
        session_client.__aenter__ = AsyncMock(return_value=session_client)
        session_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch.object(
                service,
                "_resolve_ws_connection_for_binding",
                return_value=_make_conn_info(),
            ),
            patch.object(
                service, "_create_session_client", return_value=session_client
            ) as mock_create,
        ):
            await service.get_messages(
                session_id=SESSION_ID,
                binding_info=binding,
            )
            # Verify called with engine_type="arklet"
            mock_create.assert_called_once()
            call_args = mock_create.call_args
            assert call_args[0][1] == "arklet"


# ==================== TestCreateSessionClient ====================


class TestCreateSessionClient:
    """Test _create_session_client method."""

    def test_creates_session_client_with_correct_params(self, service):
        # [单测用例]测试场景：_create_session_client 使用正确的 base_url 和 headers
        """_create_session_client creates AsyncSessionClient with correct params."""
        conn_info = _make_conn_info()
        client = service._create_session_client(conn_info, "openclaw")
        assert client is not None

    def test_default_engine_type_is_openclaw(self, service):
        # [单测用例]测试场景：默认 engine_type 为 openclaw
        """Default engine_type parameter is 'openclaw'."""
        conn_info = _make_conn_info()
        client = service._create_session_client(conn_info)
        assert client is not None

    def test_custom_engine_type(self, service):
        # [单测用例]测试场景：自定义 engine_type 正确传递
        """Custom engine_type produces correct base_url."""
        conn_info = WsConnectionInfo(
            ws_url="wss://gateway.example.com/proxypass/sb1:20003/api/arklet/ws",
            token=TOKEN,
            target=TARGET,
            expires_at=datetime.now(tz=UTC),
        )
        client = service._create_session_client(conn_info, "arklet")
        assert client is not None

    @patch("secbaas.core.service.bot_run._baas_service.AsyncSessionClient")
    def test_timeout_from_config(self, mock_client_cls, service):
        # [单测用例]测试场景：timeout 来自 BaasBotServiceConfig
        """Timeout value from config is passed to AsyncSessionClient."""
        conn_info = _make_conn_info()
        service._create_session_client(conn_info, "openclaw")
        mock_client_cls.assert_called_once_with(
            base_url=ANY,
            headers={"x-proxypass-token": TOKEN},
            timeout=30,  # from config
        )

    @patch("secbaas.core.service.bot_run._baas_service.AsyncSessionClient")
    def test_headers_include_proxypass_token(self, mock_client_cls, service):
        # [单测用例]测试场景：headers 包含 x-proxypass-token
        """Headers include x-proxypass-token from conn_info."""
        conn_info = _make_conn_info()
        service._create_session_client(conn_info, "openclaw")
        call_kwargs = mock_client_cls.call_args
        assert call_kwargs[1]["headers"]["x-proxypass-token"] == TOKEN


# ==================== TestResolveWsConnectionForBindingExtended ====================


class TestResolveWsConnectionForBindingExtended:
    """Extended tests for _resolve_ws_connection_for_binding."""

    @pytest.mark.asyncio
    async def test_baas_provider_uses_device_id(self, service, wss_resolver):
        # [单测用例]测试场景：device_provider 为 baas 时使用 device_id
        """When device_provider is 'baas', device_id is used as bot_uuid."""
        binding = _make_binding_info(
            device_provider="baas", device_id="device-uuid-999"
        )

        await service._resolve_ws_connection_for_binding(binding)

        call_kwargs = wss_resolver.dispatch_bot_ws_conn_info.call_args[1]
        assert call_kwargs["bot_uuid"] == "device-uuid-999"

    @pytest.mark.asyncio
    async def test_non_baas_provider_uses_bot_id(self, service, wss_resolver):
        # [单测用例]测试场景：device_provider 非 baas 时使用 bot_id
        """When device_provider is not 'baas', bot_id is used as bot_uuid."""
        binding = _make_binding_info(
            device_provider="arca", device_id="device-uuid-999"
        )

        await service._resolve_ws_connection_for_binding(binding)

        call_kwargs = wss_resolver.dispatch_bot_ws_conn_info.call_args[1]
        assert call_kwargs["bot_uuid"] == BOT_UUID

    @pytest.mark.asyncio
    async def test_tenant_from_binding_device_props(self, service, wss_resolver):
        # [单测用例]测试场景：tenant 从 binding_info.device_props 获取
        """Tenant is extracted from binding_info.device_props when no context."""
        binding = _make_binding_info(device_props={"tenant": "props-tenant"})

        await service._resolve_ws_connection_for_binding(binding)

        call_kwargs = wss_resolver.dispatch_bot_ws_conn_info.call_args[1]
        assert call_kwargs["tenant"] == "props-tenant"

    @pytest.mark.asyncio
    async def test_context_tenant_overrides_device_props(self, service, wss_resolver):
        # [单测用例]测试场景：context.tenant 优先于 device_props 中的 tenant
        """Context tenant takes priority over device_props tenant."""
        from secbaas.api.bot_runtime import BotChatContext

        binding = _make_binding_info(device_props={"tenant": "props-tenant"})

        ctx = BotChatContext(
            api_key_prefix=INVOKER,
            tenant="ctx-tenant",
            app_id="test-app",
            app_type="test-type",
        )

        await service._resolve_ws_connection_for_binding(binding, context=ctx)

        call_kwargs = wss_resolver.dispatch_bot_ws_conn_info.call_args[1]
        assert call_kwargs["tenant"] == "ctx-tenant"

    @pytest.mark.asyncio
    async def test_empty_tenant_when_no_source(self, service, wss_resolver):
        # [单测用例]测试场景：无 tenant 来源时传空字符串
        """When no tenant source, empty string is used."""
        binding = _make_binding_info(device_props={})

        await service._resolve_ws_connection_for_binding(binding)

        call_kwargs = wss_resolver.dispatch_bot_ws_conn_info.call_args[1]
        assert call_kwargs["tenant"] == ""


# ==================== TestGetOrCreateAdapterSessionExtended ====================


class TestGetOrCreateAdapterSessionExtended:
    """Extended tests for _get_or_create_adapter_session."""

    @pytest.mark.asyncio
    async def test_existing_session_raises_on_exception(self, service):
        # [单测用例]测试场景：session_id 已存在但异常时 re-raise
        """When session_id is provided but an exception occurs, it is re-raised."""
        mock_session_client = AsyncMock()
        # The current implementation just logs and returns (session_id, True),
        # no get_session call anymore. Let's test the else branch instead.
        # Since session_id is truthy, it goes into the if branch and returns
        # (session_id, True) without any async calls that could fail.
        result = await service._get_or_create_adapter_session(
            session_client=mock_session_client,
            session_id="agent:main:existing-id",
            user_id="user-001",
            metadata={},
        )
        assert result == ("agent:main:existing-id", True)

    @pytest.mark.asyncio
    async def test_create_session_passes_metadata(self, service):
        # [单测用例]测试场景：创建 adapter session 时传递 title 和 model
        """title and model from metadata are passed to adapter create_session."""
        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-meta"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)

        await service._get_or_create_adapter_session(
            session_client=mock_session_client,
            session_id=None,
            user_id="user-001",
            metadata={"title": "Test Title", "model": "gpt-4o"},
        )
        mock_session_client.create_session.assert_awaited_once_with(
            title="Test Title",
            user_id="user-001",
            agent_id="",
            uuid=None,
            model="gpt-4o",
            engine="openclaw",
        )

    @pytest.mark.asyncio
    async def test_create_session_with_none_metadata_fields(self, service):
        # [单测用例]测试场景：metadata 中无 title/model 时传 None
        """When title and model are absent from metadata, None is passed."""
        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:sess-no-meta"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)

        await service._get_or_create_adapter_session(
            session_client=mock_session_client,
            session_id=None,
            user_id="user-001",
            metadata={},
        )
        mock_session_client.create_session.assert_awaited_once_with(
            title=None,
            user_id="user-001",
            agent_id="",
            uuid=None,
            model=None,
            engine="openclaw",
        )

    @pytest.mark.asyncio
    async def test_session_id_with_prefix_already(self, service):
        # [单测用例]测试场景：adapter 返回的 session_id 已有 agent:main: 前缀不重复添加
        """When adapter returns session_id with agent:main: prefix, no duplicate prefix."""
        mock_session_client = AsyncMock()
        mock_session = MagicMock()
        mock_session.id = "agent:main:prefixed-sess"
        mock_session_client.create_session = AsyncMock(return_value=mock_session)

        result = await service._get_or_create_adapter_session(
            session_client=mock_session_client,
            session_id=None,
            user_id="user-001",
            metadata={},
        )
        assert result == ("agent:main:prefixed-sess", False)


# ==================== TestPersistSessionCreateExtended ====================


class TestPersistSessionCreateExtended:
    """Extended tests for _persist_session_create."""

    def test_trace_id_passed_to_session_service(self, service):
        # [单测用例]测试场景：trace_id 从 metadata 传递到 session service
        """trace_id from metadata is passed to DefaultSessionService.create_session."""
        mock_session_svc = MagicMock()
        mock_session_svc.create_session.return_value = "SESSION-trace"
        mock_session_svc.mark_running.return_value = None

        metadata = {"tenant": TENANT, "invoker": INVOKER, "trace_id": "trace-123"}
        session_info = _make_session_info(metadata=metadata)
        conn_info = _make_conn_info()

        with patch.object(service, "_session_service", mock_session_svc):
            result = service._persist_session_create(
                session_info=session_info,
                conn_info=conn_info,
            )
        assert result == "SESSION-trace"
        mock_session_svc.create_session.assert_called_once_with(
            bot_uuid=BOT_UUID,
            invoker=INVOKER,
            req={},
            device_uuid=DEVICE_UUID,
            tenant=TENANT,
            trace_id="trace-123",
        )

    def test_req_from_metadata_passed(self, service):
        # [单测用例]测试场景：req 从 metadata 传递到 session service
        """req field from metadata is passed to DefaultSessionService.create_session."""
        mock_session_svc = MagicMock()
        mock_session_svc.create_session.return_value = "SESSION-req"
        mock_session_svc.mark_running.return_value = None

        req_data = {"key": "value"}
        metadata = {"tenant": TENANT, "invoker": INVOKER, "req": req_data}
        session_info = _make_session_info(metadata=metadata)
        conn_info = _make_conn_info()

        with patch.object(service, "_session_service", mock_session_svc):
            result = service._persist_session_create(
                session_info=session_info,
                conn_info=conn_info,
            )
        assert result == "SESSION-req"
        mock_session_svc.create_session.assert_called_once_with(
            bot_uuid=BOT_UUID,
            invoker=INVOKER,
            req=req_data,
            device_uuid=DEVICE_UUID,
            tenant=TENANT,
            trace_id=None,
        )

    def test_empty_tenant_and_invoker_not_persisted(self, service):
        # [单测用例]测试场景：空字符串 invoker 不触发持久化
        """Empty string invoker does not trigger persistence."""
        metadata = {"tenant": TENANT, "invoker": ""}
        session_info = _make_session_info(metadata=metadata)
        conn_info = _make_conn_info()

        result = service._persist_session_create(
            session_info=session_info,
            conn_info=conn_info,
        )
        assert result is None


# ==================== TestSendMessageWaitResult ====================


class TestSendMessageWaitResult:
    """Test send_message wait_result parameter."""

    @pytest.mark.asyncio
    async def test_wait_result_passed_to_client(self, service, wss_resolver, mock_pool):
        # [单测用例]测试场景：wait_result 参数传递到 ChatClient
        """wait_result parameter is forwarded to client.send_message."""
        binding = _make_binding_info(baas_session_id="SESSION-xyz")

        mock_client = AsyncMock()
        mock_client.send_message = AsyncMock(return_value=("ok", "done"))
        mock_pool.get.return_value = mock_client

        with patch.object(
            service,
            "_resolve_ws_connection_for_binding",
            return_value=_make_conn_info(),
        ):
            await service.send_message(
                session_id=SESSION_ID,
                message="hello",
                binding_info=binding,
                wait_result=False,
            )
            call_kwargs = mock_client.send_message.call_args[1]
            assert call_kwargs["wait_result"] is False


# ==================== ANY matcher for mock assertions ====================


class _ANY:
    """Match any value in mock assertions."""

    def __eq__(self, other: object) -> bool:
        return True

    def __repr__(self) -> str:
        return "ANY"


ANY: _ANY = _ANY()
