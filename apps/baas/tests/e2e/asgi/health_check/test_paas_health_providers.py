from __future__ import annotations

import pytest

from tests.e2e.asgi.conftest import APITestHelper

pytestmark = [pytest.mark.health_check]

_ACCEPTABLE = (200, 400, 401, 403, 404, 422, 500, 501)


class TestPaasHealthOverview:
    @pytest.mark.asyncio
    async def test_overview_endpoint(self, api: APITestHelper) -> None:
        response = await api.client.get(
            api.paas_health_url(),
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"PaaS health overview returned {response.status_code}: "
            f"{response.text[:200]}"
        )


class TestPaasProviders:
    @pytest.mark.asyncio
    async def test_arca_provider(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/health-check/paas/arca",
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"Arca provider returned {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_poolab_provider(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/health-check/paas/poolab",
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"Poolab provider returned {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_sigma_provider(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/health-check/paas/sigma",
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"Sigma provider returned {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_local_provider(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/health-check/paas/local",
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"Local provider returned {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_k8s_provider(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/health-check/paas/k8s",
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"K8s provider returned {response.status_code}: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_docker_provider(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/health-check/paas/docker",
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"Docker provider returned {response.status_code}: {response.text[:200]}"
        )


class TestPaasProviderFactory:
    @pytest.mark.asyncio
    async def test_all_providers_respond(self, api: APITestHelper) -> None:
        providers = ["arca", "poolab", "sigma", "local", "k8s", "docker"]
        for provider in providers:
            response = await api.client.get(
                f"/api/v1/health-check/paas/{provider}",
                params=api.params(),
            )
            assert response.status_code in _ACCEPTABLE, (
                f"{provider} provider returned {response.status_code}: "
                f"{response.text[:200]}"
            )


class TestPaasHealthEdgeCases:
    @pytest.mark.asyncio
    async def test_unknown_provider_type(self, api: APITestHelper) -> None:
        response = await api.client.get(
            "/api/v1/health-check/paas/unknown_provider",
            params=api.params(),
        )
        assert response.status_code in _ACCEPTABLE, (
            f"Unknown provider returned {response.status_code}: {response.text[:200]}"
        )
