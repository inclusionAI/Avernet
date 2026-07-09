"""_ModelsPortMixin — models_list and providers_list port methods."""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("openclaw-port")


class _ModelsPortMixin:
    """Domain mixin: models and providers catalogue (token-agnostic)."""

    async def models_list(self) -> list[dict[str, Any]]:
        """Flat model-entry dicts: `models.list` first, fallback `providers.list`.

        Relocated intact from `engines/openclaw/models.py:list_models` up to the
        raw-payload extraction; the dict→Model DTO build moved to
        `core/adapters/openclaw/models.py`.  Returns `[]` when both RPCs fail.
        """
        client = await self._default_client()
        try:
            resp = await client.send_request("models.list", {}, timeout=5)
            if resp.ok and resp.payload:
                entries = (
                    resp.payload.get("models")
                    if isinstance(resp.payload, dict)
                    else resp.payload
                )
                if isinstance(entries, list):
                    # Treat as usable only when at least one entry has an id-ish
                    # field — a non-empty list of unconvertible dicts (missing id/model)
                    # must not skip the providers.list fallback (FIX 3).
                    usable = [
                        e for e in entries
                        if isinstance(e, dict) and (e.get("id") or e.get("model"))
                    ]
                    if usable:
                        return [e for e in entries if isinstance(e, dict)]
        except Exception as e:  # noqa: BLE001 — fall through to providers.list
            log.debug("[models_list] models.list unsupported: %s", e)

        try:
            resp = await client.send_request("providers.list", {}, timeout=5)
            if resp.ok and resp.payload:
                providers = (
                    resp.payload.get("providers")
                    if isinstance(resp.payload, dict)
                    else resp.payload
                )
                if isinstance(providers, list):
                    # Flatten: embed provider id into each model entry so the
                    # adapter can build Model DTOs without knowing the nesting.
                    out: list[dict[str, Any]] = []
                    for p in providers:
                        if not isinstance(p, dict):
                            continue
                        provider_id = p.get("id") or p.get("name") or "unknown"
                        for m in p.get("models", []) or []:
                            if isinstance(m, dict):
                                entry = dict(m)
                                entry.setdefault("provider", provider_id)
                                # Surface provider-level enabled/default flags
                                entry.setdefault(
                                    "enabled", p.get("enabled", True)
                                )
                                out.append(entry)
                            elif m:
                                out.append(
                                    {
                                        "id": str(m),
                                        "provider": provider_id,
                                        "enabled": p.get("enabled", True),
                                    }
                                )
                    return out
        except Exception as e:  # noqa: BLE001
            log.warning("[models_list] providers.list also failed: %s", e)

        log.warning("[models_list] no catalogue available; returning []")
        return []

    async def providers_list(self) -> list[dict[str, Any]]:
        """Raw provider dicts from `providers.list`; `[]` on failure.

        Relocated from `engines/openclaw/models.py:list_providers`.  Each dict
        may nest a `models` list; the adapter builds `Provider` DTOs.
        """
        client = await self._default_client()
        resp = await client.send_request("providers.list", {})
        if not resp.ok:
            msg = resp.error.message if resp.error else "unknown"
            log.warning("[providers_list] providers.list failed: %s", msg)
            return []
        data = resp.payload or {}
        entries = data.get("providers") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            log.warning(
                "[providers_list] unexpected payload shape: %s",
                type(entries).__name__,
            )
            return []
        return [e for e in entries if isinstance(e, dict)]
