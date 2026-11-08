"""Coding-owned, lazy backup resources shared by all restart entrypoints.

Assembly stores factories only. The strategy resolves just the selected provider
inside its existing failure boundary: missing BaaS backup capability degrades,
whereas an unavailable pinned Arca transport blocks replacement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .restart_arca import ArcaRestartTransport


class BaasRestartRuntime(Protocol):
    """Existing inventory/command capabilities consumed by coding backup."""

    def list_devices_by_bot_uuid(self, *, bot_uuid: str) -> list[dict[str, Any]]: ...

    def get_bot(self, *, bot_uuid: str) -> dict[str, Any]: ...

    def post_bots_api(self, *, path: str, payload: dict[str, Any], action: str) -> dict[str, Any]: ...


def _unavailable_baas() -> BaasRestartRuntime:
    raise RuntimeError("Coding restart backup runtime unavailable")


@dataclass(frozen=True)
class RestartBackupDependencies:
    """One dependency envelope; provider differences never escape into callers.

    Defaults support non-coding/unbound callers and deployments without Arca.
    They are unavailable capabilities, not successful backup/no-op fallbacks.
    No provider is resolved until coding backup actually selects it.
    """

    baas_runtime: Callable[[], BaasRestartRuntime] = _unavailable_baas
    arca_transport: Callable[[], ArcaRestartTransport] = ArcaRestartTransport

    @classmethod
    def for_instance(cls, runtime: BaasRestartRuntime) -> RestartBackupDependencies:
        """Adapt published/Caller runtimes without changing their external contract."""
        return cls(baas_runtime=lambda: runtime)
