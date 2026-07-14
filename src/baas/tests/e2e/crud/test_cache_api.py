"""E2E tests for cache API endpoints.

Endpoints:
- POST /api/v1/cache/{key} - Write cache entry with TTL
- GET /api/v1/cache/{key} - Read cache entry by key
"""

import pytest

pytestmark = [pytest.mark.e2e, pytest.mark.crud]


class TestCacheApi:
    """Test suite for cache CRUD operations."""

    @pytest.mark.asyncio
    async def test_set_and_get_cache(self, api, unique_id: str):
        """Test writing a cache entry then reading it back."""
        key = f"e2e-test-{unique_id}"
        value = "test-value-hello-world"
        ttl = 300

        # Set cache
        set_resp = await api.client.post(
            f"/api/v1/cache/{key}",
            json={"value": value, "ttl_seconds": ttl},
        )
        assert set_resp.status_code == 200
        set_data = set_resp.json()["data"]
        assert set_data["key"] == key
        assert set_data["ttl_seconds"] == ttl

        # Get cache
        get_resp = await api.client.get(f"/api/v1/cache/{key}")
        assert get_resp.status_code == 200
        get_data = get_resp.json()["data"]
        assert get_data["key"] == key
        assert get_data["value"] == value

    @pytest.mark.asyncio
    async def test_get_cache_not_found(self, api, unique_id: str):
        """Test reading a non-existent cache key returns 404."""
        key = f"e2e-nonexistent-{unique_id}"
        resp = await api.client.get(f"/api/v1/cache/{key}")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_set_cache_overwrite(self, api, unique_id: str):
        """Test overwriting an existing cache key."""
        key = f"e2e-overwrite-{unique_id}"

        # Write initial value
        await api.client.post(
            f"/api/v1/cache/{key}",
            json={"value": "initial", "ttl_seconds": 300},
        )

        # Overwrite with new value
        set_resp = await api.client.post(
            f"/api/v1/cache/{key}",
            json={"value": "overwritten", "ttl_seconds": 600},
        )
        assert set_resp.status_code == 200

        get_resp = await api.client.get(f"/api/v1/cache/{key}")
        assert get_resp.json()["data"]["value"] == "overwritten"

    @pytest.mark.asyncio
    async def test_set_cache_invalid_ttl_zero(self, api, unique_id: str):
        """Test that TTL of 0 is rejected (must be > 0)."""
        key = f"e2e-ttl-zero-{unique_id}"
        resp = await api.client.post(
            f"/api/v1/cache/{key}",
            json={"value": "test", "ttl_seconds": 0},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_set_cache_invalid_ttl_negative(self, api, unique_id: str):
        """Test that negative TTL is rejected."""
        key = f"e2e-ttl-neg-{unique_id}"
        resp = await api.client.post(
            f"/api/v1/cache/{key}",
            json={"value": "test", "ttl_seconds": -1},
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_set_cache_empty_value(self, api, unique_id: str):
        """Test writing a cache entry with empty string value."""
        key = f"e2e-empty-{unique_id}"
        resp = await api.client.post(
            f"/api/v1/cache/{key}",
            json={"value": "", "ttl_seconds": 60},
        )
        assert resp.status_code == 200
        body = resp.json()
        if "data" in body:
            assert body["data"].get("key") == key
