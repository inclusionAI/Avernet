"""Configurable engine admission, independent of Bot ownership checks."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends

from agentclaw.community.adapters.http.openapi_v1.admission_modes import AdmissionMode
from agentclaw.community.adapters.http.openapi_v1.admission_routes import ADMISSION
from agentclaw.community.adapters.http.openapi_v1.contracts import (
    ErrorEnvelope,
    error_example,
)
from agentclaw.community.adapters.http.openapi_v1.errors import (
    EngineOperationUnsupportedError,
)
from agentclaw.community.adapters.http.openapi_v1.error_decorator import envelope_errors


@dataclass(frozen=True)
class EngineCheck:
    """Configure ENGINE admission with allowed engines and an authorized Bot reader."""

    engines: frozenset[str]
    bots: Callable[..., Any]

    def __call__(self, endpoint: Callable[..., Any]) -> Callable[..., Any]:
        setattr(endpoint, '__openapi_engine_check__', self)
        return endpoint


def apply_engine_check(
    path: str, endpoint: Callable[..., Any], route_options: dict[str, Any]
) -> None:
    """Enforce ENGINE mode's configuration and attach its gate and error contract."""
    rule = getattr(endpoint, '__openapi_engine_check__', None)
    methods = set(route_options.get('methods') or {'GET'}) - {'HEAD', 'OPTIONS'}
    modes = {ADMISSION.get((method, path)) for method in methods}
    if modes != {AdmissionMode.ENGINE}:
        if rule is not None or AdmissionMode.ENGINE in modes:
            raise ValueError(
                f'EngineCheck requires AdmissionMode.ENGINE for every route method: {path}'
            )
        return
    if rule is None or not rule.engines:
        raise ValueError(
            f'AdmissionMode.ENGINE requires EngineCheck with a non-empty engine allowlist: {path}'
        )

    async def gate(bots: list[dict[str, Any]] = Depends(rule.bots)) -> None:
        if any(bot.get('active_engine') not in rule.engines for bot in bots):
            raise EngineOperationUnsupportedError('operation is not supported for this engine')

    route_options['dependencies'] = [
        *(route_options.get('dependencies') or []), Depends(gate),
    ]
    responses = dict(route_options.get('responses') or {})
    responses[501] = {
        'model': ErrorEnvelope,
        'description': 'Supported engines: ' + ', '.join(sorted(rule.engines)) + '.',
        **error_example(501, 'Not supported for this engine'),
    }
    route_options['responses'] = responses


def engine_checked_handler(endpoint: Callable[..., Any], handler: Callable[..., Any]) -> Callable[..., Any]:
    """Map engine-gated dependency failures using the existing public envelope."""
    if getattr(endpoint, '__openapi_engine_check__', None) is None:
        return handler
    return envelope_errors(handler)
