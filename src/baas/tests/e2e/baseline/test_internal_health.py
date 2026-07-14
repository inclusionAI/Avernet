"""E2E tests for internal health-checker endpoints."""

import pytest

from ..conftest import APITestHelper

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]

INTERNAL_ALIVE_URL = "/internal/bot-health-checker/alive"


class TestInternalHealthAliveValidation:
    """Validation-error coverage for the internal health-checker alive endpoint.

    The internal router at ``/internal/bot-health-checker/alive`` accepts:
    - ``bot_id``   (required, str, min_length=1)
    - ``entity_id``(required, str, min_length=1)
    - ``minutes``  (default 1440, ge=1)
    - ``statuses`` (optional, comma-separated str)
    - ``env``      (default ``"prod"``)
    """

    @pytest.mark.asyncio
    async def test_missing_bot_id_returns_422(self, api: APITestHelper) -> None:
        """GET without ``bot_id`` → 422."""
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={"entity_id": "some-entity"},
        )
        assert response.status_code == 422, (
            f"Expected 422 when bot_id is missing, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_missing_entity_id_returns_422(self, api: APITestHelper) -> None:
        """GET without ``entity_id`` → 422."""
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={"bot_id": "some-bot"},
        )
        assert response.status_code == 422, (
            f"Expected 422 when entity_id is missing, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_empty_env_returns_400_or_422(self, api: APITestHelper) -> None:
        """GET with ``env`` set to an empty string → 400 or 422.

        The router explicitly checks ``if not env or not env.strip()`` and
        raises a 400, so 400 is the expected code. 422 is accepted as a
        fallback in case the validation layer changes.
        """
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={"bot_id": "x", "entity_id": "y", "env": ""},
        )
        assert response.status_code in (400, 422), (
            f"Expected 400 or 422 for empty env, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_invalid_status_returns_400_or_422(self, api: APITestHelper) -> None:
        """GET with a status value that is not in the allowed set → 400 or 422.

        The router validates each status against ``VALID_STATUSES`` and returns
        400 for unknown values.
        """
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={"bot_id": "x", "entity_id": "y", "statuses": "bogus_status"},
        )
        assert response.status_code in (400, 422), (
            f"Expected 400 or 422 for invalid status, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_negative_minutes_returns_422(self, api: APITestHelper) -> None:
        """GET with ``minutes`` set to a negative value → 422.

        The ``minutes`` query param is declared with ``ge=1``, so FastAPI
        rejects values below 1 with a 422 validation error.
        """
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={"bot_id": "x", "entity_id": "y", "minutes": -5},
        )
        assert response.status_code == 422, (
            f"Expected 422 for negative minutes, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_zero_minutes_returns_422(self, api: APITestHelper) -> None:
        """GET with ``minutes=0`` → 422.

        Zero violates the ``ge=1`` constraint and should be rejected by
        FastAPI's built-in validation.
        """
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={"bot_id": "x", "entity_id": "y", "minutes": 0},
        )
        assert response.status_code == 422, (
            f"Expected 422 for zero minutes, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_nonexistent_bot_and_entity_returns_404_or_200(
        self, api: APITestHelper
    ) -> None:
        """GET with a non-existent ``bot_id`` and ``entity_id`` → 404 or 200.

        The router raises ``SandboxNotFoundError`` (→ 404) when the sandbox
        provider cannot be found. If the service returns an empty result, the
        response will be 200 with an empty payload instead.
        """
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={
                "bot_id": "nonexistent",
                "entity_id": "nonexistent",
            },
        )
        assert response.status_code in (200, 404), (
            f"Expected 200 or 404 for nonexistent bot/entity, "
            f"got {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_valid_minimal_request_returns_200_or_404(
        self, api: APITestHelper
    ) -> None:
        """GET with all required params set to sensible defaults → 200 or 404.

        This is the minimal happy-path call. It may succeed (200) when the
        configured sandbox provider finds a device, or return 404 when no
        device matches.
        """
        response = await api.client.get(
            INTERNAL_ALIVE_URL,
            params={
                "bot_id": "x",
                "entity_id": "y",
                "env": "prod",
                "minutes": 60,
            },
        )
        assert response.status_code in (200, 404), (
            f"Expected 200 or 404 for valid minimal request, "
            f"got {response.status_code}: {response.text[:200]}"
        )
