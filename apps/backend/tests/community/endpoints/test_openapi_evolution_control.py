"""RSI controls exercise the shared login/Bot gates and real internal relay."""

from agentclaw.community.adapters.http.openapi_v1.authorization_rules_evolution import (
    BASE,
    OPERATIONS,
)
from agentclaw.community.di.evolution_control_config import (
    EvolutionControlClientFactory,
)
from tests.community.factories.evolution_control import (
    BOT,
    ENTITY,
    OWNER,
    headers,
    seed_control,
)
from tests.community.framework import (
    CaseInput,
    ExpectError,
    ExpectSuccess,
    endpoint_test,
)


def _declare():
    """The framework owns invocation of each declared endpoint."""


def _check(allowed, method, suffix):
    def check(response, world):
        calls = world.get(EvolutionControlClientFactory).calls
        if not allowed:
            assert not calls
            return
        assert len(calls) == 1
        assert calls[0].method == method
        assert calls[0].url.path == f"/openapi/v1/bots/{BOT}/evolution{suffix}"
        assert response.headers["etag"] == '"next-etag"'
        assert "set-cookie" not in response.headers

    return check


for _method, _suffix in OPERATIONS:
    _payload = None
    if _method == "PUT":
        _payload = {"bindings": []}
    elif _method == "POST":
        _payload = (
            {"binding_id": "fixture-binding"}
            if _suffix == "/runs"
            else {"reason": "fixture cancel"}
        )
    for _allowed in (True, False):
        endpoint_test(
            method=_method,
            path=BASE + _suffix,
            scenario="rsi_control_authorized_relay"
            if _allowed
            else "rsi_control_requires_system_login",
            input=CaseInput(
                path_params={"bot_id": BOT, "run_id": "fixture-run"},
                query_params={"entity_id": ENTITY, "user_id": OWNER},
                headers=headers() if _allowed else {},
                json_body=_payload,
            ),
            seed=seed_control,
            expect=ExpectSuccess(
                status=202 if _method == "POST" and _suffix == "/runs" else 200,
                json_contains={"data": {"relayed": True}},
            )
            if _allowed
            else ExpectError(status=401),
            extra_assertions=(
                _check(_allowed, _method, _suffix.replace("{run_id}", "fixture-run")),
            ),
        )(_declare)
