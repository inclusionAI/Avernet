"""Real signed internal receipts; no IAM, developer login or employee identity."""

import time

import httpx
import jwt
import pytest
from fastapi import FastAPI

from avernet_evolution.adapters.backend_access import HEADER, BackendAccess, fingerprint, issue_grant
from avernet_evolution.adapters.control import build_control_router, install_errors
from avernet_evolution.composition import compose
from avernet_evolution.local import BOT, build_local_platform

KEY = "synthetic-platform-principal-key-32-bytes"
TENANT = "fixture-tenant"


def receipt(bot=BOT, **overrides):
    return issue_grant(
        **(
            {"key": KEY, "bot": bot, "actor": "collaborator", "tenant": TENANT, "request_hash": "request-one"}
            | overrides
        )
    )


def check(token, **overrides):
    return BackendAccess(key=KEY, tenant=TENANT).verify(
        token,
        **({"bot_id": BOT.bot_id, "owner_hint": BOT.entity_id, "request_hash": "request-one"} | overrides),
    )


def test_actor_is_authenticated_initiator_not_resource_owner():
    grant = check(receipt())
    assert grant.actor == "collaborator" != grant.bot.owner_id
    assert grant.bot == BOT


@pytest.mark.parametrize(
    "overrides",
    [
        {"bot_id": "other"},
        {"owner_hint": "other"},
        {"request_hash": "tampered"},
    ],
)
def test_scope_and_request_binding(overrides):
    with pytest.raises(PermissionError):
        check(receipt(), **overrides)


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "gateway"},
        {"aud": "backend"},
        {"tenant": "other"},
        {"exp": int(time.time()) - 1},
        {"actor": ""},
        {"actor": 123},
        {"exp": int(time.time()) + 3600},
        {"bot": {}},
    ],
)
def test_untrusted_claims_are_refused(overrides):
    claims = jwt.decode(receipt(), KEY, algorithms=["HS256"], audience="evolution")
    token = jwt.encode(
        claims | overrides, KEY, algorithm="HS256", headers={"typ": "avernet-evolution-grant", "kid": "bare"}
    )
    with pytest.raises(PermissionError):
        check(token)


@pytest.mark.parametrize("token", ["", "admin", "Bearer IAM_TOKEN", "unsigned.claims.token"])
def test_user_cookie_or_header_is_not_an_internal_receipt(token):
    with pytest.raises(PermissionError):
        check(token)


@pytest.mark.parametrize(
    "headers", [{"typ": "JWT", "kid": "bare"}, {"typ": "avernet-evolution-grant", "kid": "other"}]
)
def test_gateway_principal_or_other_receipt_header_is_refused(headers):
    claims = jwt.decode(receipt(), KEY, algorithms=["HS256"], audience="evolution")
    with pytest.raises(PermissionError):
        check(jwt.encode(claims, KEY, algorithm="HS256", headers=headers))


def test_receipt_signed_with_another_service_key_is_refused():
    with pytest.raises(PermissionError):
        check(receipt(key="another-synthetic-service-key-32-bytes"))


def test_weak_key_and_empty_tenant_fail_closed():
    for key, tenant in [("", TENANT), (KEY, "")]:
        with pytest.raises(ValueError):
            BackendAccess(key=key, tenant=tenant)


@pytest.mark.asyncio
async def test_control_requires_backend_receipt_even_if_local_provider_would_accept_cookie(settings):
    platform = await build_local_platform(settings)
    runtime = await compose(settings, platform)
    app = FastAPI()
    install_errors(app)
    app.include_router(build_control_router(runtime, backend_access=BackendAccess(key=KEY, tenant=TENANT)))
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://evolution.test"
        ) as c:
            path = f"/openapi/v1/bots/{BOT.bot_id}/evolution/bindings"
            params = {"entity_id": BOT.entity_id}
            assert (
                await c.get(
                    path,
                    params=params,
                    headers={
                        "Authorization": f"Bearer {settings.local_token_file.read_text()}",
                        "Cookie": "IAM_TOKEN=fixture",
                    },
                )
            ).status_code == 403
            request = c.build_request("GET", path, params=params)
            request.headers[HEADER] = receipt(
                request_hash=fingerprint(
                    "GET", request.url.path, request.url.query.decode(), request.content, request.headers
                )
            )
            result = await c.send(request)
            assert result.status_code == 200
            assert result.headers["etag"] == '""'
            # Same proof cannot mutate or change resource scope.
            for mutated in [
                c.build_request("PUT", path, params=params, headers=request.headers, json={"bindings": []}),
                c.build_request("GET", path, params={"entity_id": "other"}, headers=request.headers),
            ]:
                assert (await c.send(mutated)).status_code == 403
    finally:
        await runtime.close()


def test_production_settings_require_shared_internal_trust(settings):
    from avernet_evolution.config import Settings

    values = settings.model_dump() | {"profile": "production", "platform_factory": "provider:build"}
    with pytest.raises(ValueError, match="Backend principal key file and tenant"):
        Settings(**values)
    assert (
        Settings(
            **(values | {"backend_principal_key_file": settings.signing_key_file, "backend_tenant": TENANT})
        ).profile
        == "production"
    )


def test_production_lifecycle_uses_shared_receipt_and_pins_tenant(settings, tmp_path):
    """Real composition/lifespan, fixture platform; never launches a remote worker."""
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from avernet_evolution.bootstrap import create_app
    from avernet_evolution.config import Settings
    from avernet_evolution.core.evolution_strategy.registration import Runtime, RuntimeKind

    key_file = tmp_path / "system-principal-key"
    key_file.write_text(KEY)
    production = Settings(
        **(
            settings.model_dump()
            | {
                "profile": "production",
                "platform_factory": "fixture_platform:build",
                "backend_principal_key_file": key_file,
                "backend_tenant": TENANT,
            }
        )
    )

    async def fixture_factory(config):
        platform = await build_local_platform(config)
        record = (await platform.registry.list())[0]
        image = "example.test/worker@sha256:" + "a" * 64
        changed = replace(
            record,
            registration=replace(record.registration, runtime=Runtime(RuntimeKind.JOB_WORKER, image=image)),
        )
        platform.registry._records[(record.registration.id, record.registration.version)] = changed
        platform.sandbox, platform.worker_image = object(), image
        return platform

    path = f"/openapi/v1/bots/{BOT.bot_id}/evolution/bindings"
    with TestClient(create_app(production, platform_factory=fixture_factory)) as client:
        assert (
            client.get(
                path,
                params={"owner_id": BOT.owner_id},
                headers={"Authorization": f"Bearer {settings.local_token_file.read_text()}"},
            ).status_code
            == 403
        )
        token = receipt(request_hash=fingerprint("GET", path, f"entity_id={BOT.entity_id}", b"", {}))
        assert (
            client.get(path, params={"entity_id": BOT.entity_id}, headers={HEADER: token}).status_code == 200
        )
        assert client.app.state.runtime.runner._task is None  # consumer disabled
    with TestClient(create_app(production, platform_factory=fixture_factory)):
        pass  # same deployment/database is restartable
    other_tenant = production.model_copy(update={"backend_tenant": "another-tenant"})
    with (
        pytest.raises(ValueError, match="immutable deployment identity changed: control-scope"),
        TestClient(create_app(other_tenant, platform_factory=fixture_factory)),
    ):
        pass


def test_invalid_production_trust_fails_before_platform_or_database(settings):
    from fastapi.testclient import TestClient

    from avernet_evolution.bootstrap import create_app

    production = settings.model_copy(
        update={
            "profile": "production",
            "platform_factory": "fixture_platform:build",
            "backend_principal_key_file": settings.database.parent / "missing-key",
            "backend_tenant": TENANT,
        }
    )

    async def must_not_run(config):
        raise AssertionError("platform must not start with invalid service trust")

    with (
        pytest.raises(FileNotFoundError),
        TestClient(create_app(production, platform_factory=must_not_run)),
    ):
        pass
    assert not settings.database.exists()
