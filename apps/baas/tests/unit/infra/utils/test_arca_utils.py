"""Unit tests for arca_utils module.

Covers all key functions: URL building, secret retrieval, header building,
HTTP operations, and more.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from secbaas.community.config import Config
from secbaas.community.plugins.sandbox.utils import arca_utils

_PROXY_CONFIG = Config(
    user_config={
        "agentclawproxy": {
            "host": {
                "dev": "ac-proxy-dev.test",
                "pre": "ac-proxy-pre.test",
                "prod": "ac-proxy-prod.test",
            },
        },
    },
)


@pytest.fixture
def arca_tool() -> arca_utils.ArcaUtils:
    """Construct an ArcaUtils instance with a mock secret plugin."""
    plugin = MagicMock()
    plugin.get_secret.return_value = "test-secret"
    return arca_utils.ArcaUtils(secret_plugin=plugin)


# ── Test helpers ──────────────────────────────────────────────────────────────


class FakeAsyncClient:
    """Fake httpx.AsyncClient that works with async context manager and can
    be configured with a post() side effect."""

    def __init__(self, post_side_effect=None):
        self.post_side_effect = post_side_effect
        self.post_args = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, *args, **kwargs):
        self.post_args.append((args, kwargs))
        if isinstance(self.post_side_effect, Exception):
            raise self.post_side_effect
        if callable(self.post_side_effect):
            return self.post_side_effect()
        return self.post_side_effect


# ── Constants tests ────────────────────────────────────────────────────────────


class TestConstants:
    """Tests for module-level constants."""

    def test_bolt_port(self):
        """WHEN accessing BOLT_PORT, THEN returns 20003."""
        assert arca_utils.BOLT_PORT == 20003

    def test_proxypass_secret_name(self):
        """WHEN accessing PROXYPASS_SECRET_NAME, THEN returns correct MIST secret name."""
        assert (
            arca_utils.PROXYPASS_SECRET_NAME
            == "other_manual_agentclawproxy_proxypass_secret"
        )

    def test_arca_skills_local_dir(self):
        """WHEN accessing ARCA_SKILLS_LOCAL_DIR, THEN returns correct path."""
        assert (
            arca_utils.ARCA_SKILLS_LOCAL_DIR == "/home/admin/.extra-skills/skills-local"
        )


# ── URL building tests ─────────────────────────────────────────────────────────


class TestGetArcaProxyBaseUrl:
    """Tests for _get_arca_proxy_base_url function."""

    def test_returns_pre_url_when_env_is_pre(self, monkeypatch, arca_tool):
        """WHEN current env is 'pre', THEN returns pre-prod URL."""
        monkeypatch.setenv("SERVER_ENV", "pre")
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            result = arca_tool._get_arca_proxy_base_url()
            assert result == "https://ac-proxy-pre.test"

    def test_returns_prod_url_when_env_is_prod(self, monkeypatch, arca_tool):
        """WHEN current env is 'prod', THEN returns prod URL."""
        monkeypatch.setenv("SERVER_ENV", "prod")
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            result = arca_tool._get_arca_proxy_base_url()
            assert result == "https://ac-proxy-prod.test"

    def test_returns_prod_url_when_env_is_gray(self, monkeypatch, arca_tool):
        """WHEN current env is 'gray' (which maps to prod), THEN returns prod URL."""
        monkeypatch.setenv("SERVER_ENV", "gray")
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            result = arca_tool._get_arca_proxy_base_url()
            assert result == "https://ac-proxy-prod.test"

    def test_returns_dev_url_when_env_is_dev(self, monkeypatch, arca_tool):
        """WHEN current env is 'dev', THEN returns dev URL."""
        monkeypatch.setenv("SERVER_ENV", "dev")
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            result = arca_tool._get_arca_proxy_base_url()
            assert result == "https://ac-proxy-dev.test"


class TestGetArcaTarget:
    """Tests for _get_arca_target function."""

    def test_formats_target_with_sandbox_id(self, arca_tool):
        """WHEN _get_arca_target is called with sandbox_id, THEN returns formatted target."""
        result = arca_tool._get_arca_target("sb-12345")
        assert result == f"ARCA_sb-12345:{arca_utils.BOLT_PORT}"

    def test_formats_target_with_numeric_sandbox_id(self, arca_tool):
        """WHEN called with numeric sandbox_id, THEN formats correctly."""
        result = arca_tool._get_arca_target("987654")
        assert result == "ARCA_987654:20003"

    def test_formats_target_with_hyphenated_sandbox_id(self, arca_tool):
        """WHEN called with hyphenated sandbox_id, THEN formats correctly."""
        result = arca_tool._get_arca_target("arca-sb-test-001")
        assert result == "ARCA_arca-sb-test-001:20003"


class TestGetBoltUrl:
    """Tests for _get_bolt_url function."""

    def test_builds_correct_bolt_url(self, monkeypatch, arca_tool):
        """WHEN _get_bolt_url is called, THEN builds correct full URL."""
        monkeypatch.setenv("SERVER_ENV", "pre")
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            result = arca_tool._get_bolt_url("sb-1", "/api/file/read")
            expected = (
                "https://ac-proxy-pre.test/proxypass/ARCA_sb-1:20003/api/file/read"
            )
            assert result == expected

    def test_builds_correct_bolt_url_with_upload_path(self, monkeypatch, arca_tool):
        """WHEN _get_bolt_url is called in prod, THEN builds correct URL."""
        monkeypatch.setenv("SERVER_ENV", "prod")
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            result = arca_tool._get_bolt_url("sandbox-x", "/api/file/upload")
            expected = "https://ac-proxy-prod.test/proxypass/ARCA_sandbox-x:20003/api/file/upload"
            assert result == expected


class TestBuildProxypassUrl:
    """Tests for ArcaUtils.build_proxypass_url (proxypass URL builder)."""

    def test_builds_wss_url(self, arca_tool):
        """WHEN build_proxypass_url is called, THEN delegates to proxypass_utils."""
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.proxypass_utils.build_proxypass_url",
            return_value="wss://ac-proxy-dev.test/proxypass/TECLAW_b-1:8080/ws",
        ) as mock_build:
            result = arca_tool.build_proxypass_url(
                "TECLAW_b-1:8080", "/ws", scheme="wss"
            )
            assert result == "wss://ac-proxy-dev.test/proxypass/TECLAW_b-1:8080/ws"
            mock_build.assert_called_once_with("TECLAW_b-1:8080", "/ws", scheme="wss")

    def test_builds_https_url(self, arca_tool):
        """WHEN build_proxypass_url uses https, THEN forwards scheme."""
        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.proxypass_utils.build_proxypass_url",
            return_value="https://ac-proxy-pre.test/proxypass/TECLAW_b-1:8080/api",
        ) as mock_build:
            result = arca_tool.build_proxypass_url(
                "TECLAW_b-1:8080", "/api", scheme="https"
            )
            assert result == "https://ac-proxy-pre.test/proxypass/TECLAW_b-1:8080/api"
            mock_build.assert_called_once_with(
                "TECLAW_b-1:8080", "/api", scheme="https"
            )


# ── Secret retrieval tests ─────────────────────────────────────────────────────


class TestProxypassTokenAndHeaders:
    """Tests for _get_proxypass_token and _get_proxypass_headers functions."""

    def test_get_proxypass_token_builds_jwt(self, arca_tool):
        """WHEN _get_proxypass_token is called, THEN builds JWT from MIST secret."""
        with patch.object(
            arca_utils.secret_utils,
            "generate_jwt_token",
            return_value="fake-jwt-token",
        ) as mock_jwt:
            result = arca_tool._get_proxypass_token("sb-42")
            assert result == "fake-jwt-token"
            mock_jwt.assert_called_once_with("ARCA_sb-42:20003", "test-secret", ttl=300)

    def test_get_proxypass_token_respects_custom_ttl(self, arca_tool):
        """WHEN _get_proxypass_token is called with ttl, THEN forwards ttl to JWT."""
        with patch.object(
            arca_utils.secret_utils,
            "generate_jwt_token",
            return_value="fake-jwt-token",
        ) as mock_jwt:
            result = arca_tool._get_proxypass_token("sb-42", ttl=120)
            assert result == "fake-jwt-token"
            mock_jwt.assert_called_once_with("ARCA_sb-42:20003", "test-secret", ttl=120)

    def test_get_proxypass_headers_builds_correct_structure(self, arca_tool):
        """WHEN _get_proxypass_headers is called, THEN returns dict with x-proxypass-token."""
        with patch.object(
            arca_utils.secret_utils, "generate_jwt_token", return_value="my-token"
        ):
            headers = arca_tool._get_proxypass_headers("sb-1")
            assert headers == {"x-proxypass-token": "my-token"}


# ── File API tests (async HTTP) ────────────────────────────────────────────────


class TestUploadToArca:
    """Tests for upload_to_arca async function."""

    @pytest.mark.asyncio
    async def test_uploads_successfully(self, monkeypatch, arca_tool):
        """WHEN upload_to_arca is called, THEN POSTs to Bolt and returns result."""
        monkeypatch.setenv("SERVER_ENV", "pre")

        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "ok", "path": "/data/test.txt"}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock()

        with (
            patch.object(
                arca_utils.secret_utils, "generate_jwt_token", return_value="tok"
            ),
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(
                "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
                return_value=_PROXY_CONFIG,
            ),
        ):
            result = await arca_tool.upload_to_arca(b"hello", "/data/test.txt", "sb-1")
            assert result == {"status": "ok", "path": "/data/test.txt"}

    @pytest.mark.asyncio
    async def test_raises_value_error_for_none_sandbox_id(self, arca_tool):
        """WHEN sandbox_id is None, THEN raises ValueError."""
        with pytest.raises(ValueError, match="Sandbox ID is required"):
            await arca_tool.upload_to_arca(b"data", "/path", None)

    @pytest.mark.asyncio
    async def test_raises_on_http_error(self, monkeypatch, arca_tool):
        """WHEN HTTP POST fails with HTTPStatusError, THEN re-raises the exception."""
        monkeypatch.setenv("SERVER_ENV", "pre")

        err = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=MagicMock(status_code=500)
        )
        fake_client = FakeAsyncClient(post_side_effect=err)

        with (
            patch.object(
                arca_utils.secret_utils, "generate_jwt_token", return_value="tok"
            ),
            patch("httpx.AsyncClient", return_value=fake_client),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await arca_tool.upload_to_arca(b"data", "/path", "sb-1")


# ── Integration-style tests ─────────────────────────────────────────────────────


class TestArcaUtilsEndToEnd:
    """End-to-end style tests that chain multiple internal functions."""

    def test_full_url_building_chain_pre_env(self, monkeypatch, arca_tool):
        """WHEN building URLs in pre env, THEN the full chain works correctly."""
        monkeypatch.setenv("SERVER_ENV", "pre")

        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            base = arca_tool._get_arca_proxy_base_url()
            target = arca_tool._get_arca_target("sb-test")
            bolt_url = arca_tool._get_bolt_url("sb-test", "/api/file/read")

        assert base == "https://ac-proxy-pre.test"
        assert target == "ARCA_sb-test:20003"
        assert (
            bolt_url
            == "https://ac-proxy-pre.test/proxypass/ARCA_sb-test:20003/api/file/read"
        )

    def test_full_url_building_chain_prod_env(self, monkeypatch, arca_tool):
        """WHEN building URLs in prod env, THEN the full chain works correctly."""
        monkeypatch.setenv("SERVER_ENV", "prod")

        with patch(
            "secbaas.community.plugins.sandbox.utils.arca_utils.get_config",
            return_value=_PROXY_CONFIG,
        ):
            base = arca_tool._get_arca_proxy_base_url()
            target = arca_tool._get_arca_target("prod-sb")
            bolt_url = arca_tool._get_bolt_url("prod-sb", "/api/file/upload")

        assert base == "https://ac-proxy-prod.test"
        assert target == "ARCA_prod-sb:20003"
        assert bolt_url.startswith("https://ac-proxy-prod.test")
