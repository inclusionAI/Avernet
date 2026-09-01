"""Device-bootstrap projection for the managed CLI Passport scope."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from agentclaw.community.core.devices.errors import DeviceServiceError


class CliScopeReconcilerProtocol(Protocol):
    """The narrow CLI scope dependency needed by device bootstrap."""

    def reconcile(self, *, bot: Mapping[str, Any]) -> Any: ...


def reconcile_cli_bootstrap_scope(
    scope_reconciler: CliScopeReconcilerProtocol | None,
    *,
    bot: Mapping[str, Any],
    device_id: str,
    bot_id: str,
    logger: Any,
) -> dict[str, object]:
    """Reconcile before engine start and return only non-sensitive metadata."""
    if scope_reconciler is None:
        return {}
    try:
        scope = scope_reconciler.reconcile(bot=bot)
    except Exception as exc:
        logger.error(
            "cli_passport_reconcile_failed device_id=%s bot_id=%s error_type=%s",
            device_id,
            bot_id,
            type(exc).__name__,
        )
        raise DeviceServiceError("CLI passport scope reconciliation failed") from exc
    return {
        "cli_manifest_version": scope.manifest_version,
        "cli_manifest_digest": scope.manifest_digest,
        "cli_codes": list(scope.cli_codes),
    }


__all__ = ["CliScopeReconcilerProtocol", "reconcile_cli_bootstrap_scope"]
