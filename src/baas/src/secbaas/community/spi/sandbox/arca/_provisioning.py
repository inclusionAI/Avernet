"""Engine-level Arca provisioning extension point (BaaS mirror of backend strategy).

Backend ``EngineProvisioningStrategy`` assembles an opaque ``extra_properties``
envelope per engine; this module consumes it at the Arca boundary. One strategy
per engine namespace owns *every* interpretation that engine needs at sandbox
creation — not one resolver class per field. ``resolve_request_api_key`` is the
first concrete hook (theta_key decryption); future engine-owned Arca-side
fields (metadata overlays, env overlays, ...) land as additional methods on the
same strategy class, and the registry stays the single dispatch seam.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from secbaas.community.logger import get_logger

logger = get_logger()


class ArcaProvisioningStrategy(ABC):
    """Per-engine-namespace provisioning hooks at the Arca creation boundary.

    Concrete engines subclass this ABC so the contract is explicit and missing
    hooks fail at import time. Each strategy owns one ``namespace`` (the key
    under which its properties travel inside ``extra_properties``) and every
    interpretation that namespace needs at the Arca boundary.
    """

    @property
    @abstractmethod
    def namespace(self) -> str:
        """Property namespace this strategy consumes (e.g. ``"aicoding"``)."""

    def resolve_request_api_key(
        self, extra_properties: dict[str, Any] | None
    ) -> str | None:
        """Return a request-scoped Arca API key, or ``None`` to keep fixed creds.

        Default no-op. Concrete strategies override this when their namespace
        carries a credential field (e.g. theta_key). Returning ``None`` for any
        missing/empty/illegal value preserves the legacy fixed-credential path.
        """
        return None

    # Future hooks for engine-owned Arca-side fields land here as additional
    # methods (resolve_runtime_metadata / resolve_envs_overlay / ...), not as
    # new resolver classes.


class ArcaProvisioningRegistry:
    """Dispatch ``extra_properties`` to the strategy that owns its namespace.

    The registry is the composition seam: ``ArcaPaasService`` calls a generic
    entry point and stays unaware of any engine or field name. Each registered
    strategy independently decides whether its namespace applies. Dispatch is
    fail-open — any failure falls back to ``None`` so generic provisioning keeps
    its legacy fixed-credential path.
    """

    def __init__(
        self, strategies: list[ArcaProvisioningStrategy] | None = None
    ) -> None:
        self._strategies: dict[str, ArcaProvisioningStrategy] = {}
        for strategy in strategies or ():
            self.register(strategy)

    def register(self, strategy: ArcaProvisioningStrategy) -> None:
        namespace = strategy.namespace
        if namespace in self._strategies:
            raise ValueError(
                f"arca provisioning strategy already registered: {namespace}"
            )
        self._strategies[namespace] = strategy

    def resolve_request_api_key(
        self, extra_properties: dict[str, Any] | None
    ) -> str | None:
        """Resolve a request-scoped API key from engine-owned properties.

        Only the strategy whose namespace is present in ``extra_properties`` is
        consulted, so unrelated engines never execute credential code. Returns
        ``None`` when no matching namespace exists, the value is absent/illegal,
        or resolution fails (fail-open, preserves legacy fixed credential).
        """
        if not isinstance(extra_properties, dict) or not extra_properties:
            return None
        for namespace, strategy in self._strategies.items():
            if namespace not in extra_properties:
                continue
            try:
                return strategy.resolve_request_api_key(extra_properties)
            except Exception as exc:
                logger.warning(
                    "[arca_credential] fallback=fixed reason=extension_failed "
                    "namespace=%s error_type=%s",
                    namespace,
                    type(exc).__name__,
                )
                return None
        return None


__all__ = [
    "ArcaProvisioningStrategy",
    "ArcaProvisioningRegistry",
]
