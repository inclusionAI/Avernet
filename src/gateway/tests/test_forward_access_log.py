"""The forward log line names the tenant-bearing callers it resolved.

The gateway is the only hop that sees the caller's credentials, so it is the
only one that can say what a request turned out to *be*. Until this line carried
the identity set, a forwarded request produced a log entry that named a method,
a path and a status and left the operator no way to tell which tenant it ran
under — the question every public-API incident opens with.

Two things are pinned here: the projection from an identity set to the line's
fields (unit, exhaustive over the Principal union), and the fact that the line
the running gateway emits actually carries them.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from gateway.community.adapters.web._forward import _identity_label
from gateway.community.adapters.web.app import create_app
from gateway.community.plugins.principal_signer.bare import (
    BarePrincipalSigner,
    PrincipalSignerConfig,
)
from gateway.community.spi.auth import AuthenticatedUser
from gateway.community.spi.authn import (
    AccessKey,
    AccessKeyPrincipal,
    AppPrincipal,
    Bot,
    BotPrincipal,
    PrincipalType,
    ThirdPartyApp,
    UserPrincipal,
)
from gateway.community.spi.forwarder import ForwardRequest, ForwardResponse

_TEST_KEY = "access-log-test-shared-secret-32b!!"


def _user(user_id: str = "u-42") -> UserPrincipal:
    return UserPrincipal(
        subject=AuthenticatedUser(id=user_id, username="alice@example.com"),
    )


def _app_principal(tenant: str = "acme") -> AppPrincipal:
    return AppPrincipal(
        tenant=tenant,
        app=ThirdPartyApp(app_id=7, app_name="A", owners="o", tenant=tenant),
    )


# ── the projection ───────────────────────────────────────────────────────────


def test_no_identity_reads_as_absent() -> None:
    """An unauthenticated route forwards too, and its line must say so plainly."""
    assert _identity_label({}) == ("-", "-")


def test_user_identity_has_no_tenant_and_names_subject() -> None:
    tenant, caller = _identity_label({PrincipalType.USER: _user()})
    assert (tenant, caller) == ("-", "user:u-42")


def test_every_identity_in_the_set_is_named() -> None:
    """A user+app route answers for two identities; one of them is not enough."""
    tenant, caller = _identity_label(
        {PrincipalType.USER: _user(), PrincipalType.APP: _app_principal()}
    )
    assert tenant == "acme"
    assert caller == "user:u-42+app:7"


def test_bot_and_access_key_identities_name_their_own_ids() -> None:
    labels = _identity_label(
        {
            PrincipalType.BOT: BotPrincipal(
                tenant="acme",
                bot=Bot(
                    bot_uuid="bot-1",
                    owner_id="u-42",
                    token="secret-bot-token",
                    app_id=7,
                    agent_code="ac",
                    tenant="acme",
                ),
            ),
            PrincipalType.ACCESS_KEY: AccessKeyPrincipal(
                tenant="acme",
                access_key=AccessKey(
                    access_key="ak-1",
                    access_key_token="secret-access-key-token",
                    expire_at=datetime(2030, 1, 1, tzinfo=UTC),
                ),
            ),
        }
    )[1]
    assert labels == "bot:bot-1+access_key:ak-1"
    # The credentials the gateway forwards are never the ones it prints.
    assert "secret-bot-token" not in labels
    assert "secret-access-key-token" not in labels


def test_disagreeing_tenants_are_all_named() -> None:
    """A set that names two tenants is a bug the line must not hide.

    The downstream verifier refuses such a token outright, so a line that
    silently picked one of them would describe a request that cannot exist.
    """
    tenant, _ = _identity_label(
        {
            PrincipalType.BOT: BotPrincipal(
                tenant="tenant-a",
                bot=Bot(
                    bot_uuid="bot-1",
                    owner_id="u-42",
                    token="secret-bot-token",
                    app_id=7,
                    agent_code="ac",
                    tenant="tenant-a",
                ),
            ),
            PrincipalType.APP: _app_principal(tenant="tenant-b"),
        }
    )
    assert tenant == "tenant-a+tenant-b"


# ── the emitted line ─────────────────────────────────────────────────────────


class _StubAuthenticator:
    async def authenticate(self, method: str, path: str, creds: object) -> dict:
        return {PrincipalType.USER: _user(), PrincipalType.APP: _app_principal()}


class _StubForwarder:
    @asynccontextmanager
    async def forward(self, request: ForwardRequest):
        async def _empty_body():
            if False:  # pragma: no cover
                yield

        yield ForwardResponse(status_code=200, headers=[], body=_empty_body())


@pytest.fixture
def app():
    app = create_app()
    app.state.authenticator = _StubAuthenticator()
    app.state.forwarder = _StubForwarder()
    app.state.principal_signer = BarePrincipalSigner(
        PrincipalSignerConfig(signing_key=_TEST_KEY)
    )
    return app


async def test_successful_forward_logs_tenant_caller_and_upstream(
    app, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/openapi/v1/bots?page=2")

    assert response.status_code == 200
    line = next(
        message
        for message in (record.getMessage() for record in caplog.records)
        if message.startswith("forward GET /openapi/v1/bots")
    )
    assert "status=200" in line
    assert "tenant=acme" in line
    assert "caller=user:u-42+app:7" in line
    # Which domain claimed the path and which upstream served it — the two
    # routing decisions a forwarded request makes.
    assert "domain=bots" in line
    assert "server=backend" in line


async def test_forwarded_query_credentials_are_redacted(
    app, caplog: pytest.LogCaptureFixture
) -> None:
    """The upstream URL carries the caller's query, credentials included.

    The filter installed for uvicorn's own request lines does not cover this
    logger, so the redaction has to happen where the line is formatted.
    """
    with caplog.at_level(logging.INFO):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/openapi/v1/bots?x-proxypass-token=super-secret")

    line = next(
        message
        for message in (record.getMessage() for record in caplog.records)
        if message.startswith("forward GET /openapi/v1/bots")
    )
    assert "super-secret" not in line
    assert "x-proxypass-token=<redacted>" in line
