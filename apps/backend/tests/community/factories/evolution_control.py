"""Real Bot/principal fixtures; only the independent HTTP service is substituted."""

import time

import httpx
import jwt
from agentclaw.community.core.repository.protocols.bot import BotRepository
from agentclaw.community.di.evolution_control_config import (
    EvolutionControlClientFactory,
    EvolutionControlConfig,
)
from agentclaw.community.utils.avernet_tenant import (
    DEFAULT_AVERNET_TENANT,
    avernet_tenant_scope,
)
from agentclaw.community.utils.gateway_principal_config import (
    init_principal_verifier_config,
)
from avernet_evolution.adapters.backend_access import HEADER, BackendAccess, fingerprint

BOT = "rsi-control-fixture-bot"
OWNER = "rsi-control-fixture-owner"
ENTITY = "rsi-control-fixture-entity"
KEY = "rsi-control-fixture-signing-key-for-tests-only"


class _Secret:
    secret_user = "test"
    secret_value = KEY


class _Resolver:
    def get_secret(self, _name):
        return _Secret()


def headers():
    now = int(time.time())
    return {
        "X-Avernet-Principal": jwt.encode(
            {
                "iss": "gateway",
                "aud": "backend",
                "iat": now,
                "exp": now + 3600,
                "principals": [
                    {
                        "type": "user",
                        "subject": {"id": OWNER, "username": "owner@example.test"},
                    }
                ],
            },
            KEY,
            algorithm="HS256",
        ),
        "If-Match": '"fixture-etag"',
        "Idempotency-Key": "fixture-submit-key",
        "Cookie": "fixture-browser-cookie=never-relay",
        "Authorization": "Bearer never-relay",
    }


class FixtureEvolutionClients(EvolutionControlClientFactory):
    """Replace the HTTP edge only; the real relay signs and sends every request."""

    def __init__(self, config):
        super().__init__(config)
        self.calls = []

    def open(self):
        return httpx.AsyncClient(
            base_url=self.config.upstream,
            transport=httpx.MockTransport(self.receive),
            trust_env=False,
            follow_redirects=False,
        )

    def receive(self, request):
        access = BackendAccess(key=KEY, tenant=DEFAULT_AVERNET_TENANT).verify(
            request.headers[HEADER],
            bot_id=BOT,
            owner_hint=ENTITY,
            request_hash=fingerprint(
                request.method,
                request.url.path,
                request.url.query.decode(),
                request.content,
                request.headers,
            ),
        )
        assert access.actor == OWNER
        assert access.bot.entity_id == ENTITY and access.bot.owner_id == OWNER
        assert (
            "cookie" not in request.headers and "authorization" not in request.headers
        )
        assert "X-Avernet-Principal" not in request.headers
        assert request.url.params["entity_id"] == ENTITY
        assert "user_id" not in request.url.params
        assert request.headers["if-match"] == '"fixture-etag"'
        assert request.headers["idempotency-key"] == "fixture-submit-key"
        self.calls.append(request)
        status = (
            202
            if request.method == "POST" and request.url.path.endswith("/runs")
            else 200
        )
        return httpx.Response(
            status,
            json={"code": status * 1000, "message": "OK", "data": {"relayed": True}},
            headers={"ETag": '"next-etag"', "Set-Cookie": "never-return=1"},
        )


def seed_control(world):
    init_principal_verifier_config(_Resolver(), "fixture-key", strict=False)
    config = EvolutionControlConfig(upstream="http://evolution.test")
    world.injector.binder.bind(EvolutionControlConfig, to=config)
    world.injector.binder.bind(
        EvolutionControlClientFactory, to=FixtureEvolutionClients(config)
    )
    with avernet_tenant_scope(DEFAULT_AVERNET_TENANT):
        world.get(BotRepository).insert(
            {
                "bot_id": BOT,
                "bot_name": "RSI control fixture",
                "owner_id": OWNER,
                "owner_name": "fixture owner",
                "entity_id": ENTITY,
                "entity_type": "staff",
                "creator_id": OWNER,
                "status": "ACTIVE",
                "active_engine": "openclaw",
                "bot_type": "personal",
            }
        )
