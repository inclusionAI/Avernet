"""Deployment config for the public API's ``user_id`` self-confirm bypass.

Holds the ``OpenApiUserIdBypassConfig`` dataclass OUT of ``di/config.py`` (which
sits at zero headroom under the 1000-line architecture cap). The matching DI
provider lives in ``di/modules/openapi_user_id_bypass_config_module.py``
(registered in ``di/container.py`` alongside ``ConfigModule``); the consumer is
``adapters/http/openapi_v1/principal.py::require_user_id``.

Every delegable user-scoped ``/openapi/v1`` operation names the end user it acts
for in a ``user_id`` query parameter, and a caller authenticated as a person may
name only themselves (403 otherwise). This block lists the ``user_id`` values
that are accepted **as-is**, whoever the verified caller is — the comparison is
skipped for them. It is a deployment-internal escape hatch for shared or
service accounts in non-production environments, which is why it ships empty
and why the provider refuses a non-empty list in ``pre`` / ``prod``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OpenApiUserIdBypassConfig:
    """``openapi`` block — ``user_id`` values exempt from the self-confirm check.

    Sourced from ``user_config.openapi.user_id_bypass`` by the
    ``OpenApiUserIdBypassConfigModule`` provider. Empty (the default and the only
    value allowed in ``pre`` / ``prod``) means every human caller must name
    themselves, exactly as before the block existed.
    """

    user_ids: frozenset[str] = frozenset()


__all__ = ["OpenApiUserIdBypassConfig"]
