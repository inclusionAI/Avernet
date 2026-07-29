from __future__ import annotations

import pytest
from fastapi import FastAPI

pytestmark = [pytest.mark.e2e, pytest.mark.baseline]


class TestDIContainerBootstrap:
    def test_get_container_returns_non_null(self) -> None:
        from gateway.community.bootstrap import get_container, set_container

        set_container(None)

        from gateway.community.adapters.web.app import create_app

        app = create_app()
        try:
            container = get_container()
            assert container is not None
        finally:
            from gateway.community.bootstrap import shutdown_services

            container = get_container()
            if container is not None:
                shutdown_services(container)
            set_container(None)

    def test_plugins_provider_resolves(self) -> None:
        from gateway.community.bootstrap import get_container, set_container

        set_container(None)

        from gateway.community.adapters.web.app import create_app

        _app = create_app()  # noqa: F841
        try:
            container = get_container()
            plugins = container.plugins()

            db = plugins.database()
            assert db is not None

            forwarder = plugins.forwarder()
            assert forwarder is not None

            catalog = plugins.schema_catalog()
            assert catalog is not None

            cache = plugins.cache_plugin()
            assert cache is not None

            validator = plugins.app_token_validator()
            assert validator is not None

            resolver = plugins.tenant_resolver()
            assert resolver is not None
        finally:
            from gateway.community.bootstrap import shutdown_services

            container = get_container()
            if container is not None:
                shutdown_services(container)
            set_container(None)

    def test_fixture_creates_fresh_app_by_fastapi_type(
        self, app_no_lifespan: FastAPI
    ) -> None:
        from gateway.community.bootstrap import get_container

        container = get_container()
        assert container is not None, (
            "DI container must be initialized by the fixture's app creation"
        )

    def test_app_no_lifespan_has_noop_lifespan(self, app_no_lifespan: FastAPI) -> None:
        lifespan = app_no_lifespan.router.lifespan_context
        assert lifespan is not None
