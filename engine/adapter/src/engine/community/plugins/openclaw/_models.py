"""_ModelsPortMixin — models_list / providers_list / default_model port methods."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from engine.community.core.models.exceptions import DefaultModelNotConfiguredError

log = logging.getLogger("openclaw-port")

_OPENCLAW_CONFIG_FILENAME = "openclaw.json"
_DEFAULT_MODEL_KEY = "agents.defaults.model"


def _openclaw_config_path() -> Path:
    """Where OpenClaw keeps its live config: ``~/.openclaw/openclaw.json``.

    The adapter runs beside the gateway in the same home; the deployment
    does not relocate the file, so no env lookup.
    """
    return Path.home() / ".openclaw" / _OPENCLAW_CONFIG_FILENAME


def _default_model_from_config(config: Any) -> tuple[str, list[str]] | None:
    """Extract ``(primary, fallbacks)`` from an OpenClaw config object.

    ``agents.defaults.model`` is either a plain ``provider/model`` string or
    ``{"primary": str, "fallbacks": [str, ...]}`` (openclaw docs,
    gateway/configuration-reference). Returns ``None`` when unset / malformed.
    """
    if not isinstance(config, dict):
        return None
    agents = config.get("agents")
    defaults = agents.get("defaults") if isinstance(agents, dict) else None
    model = defaults.get("model") if isinstance(defaults, dict) else None
    if isinstance(model, str):
        primary = model.strip()
        return (primary, []) if primary else None
    if isinstance(model, dict):
        primary = model.get("primary")
        if not isinstance(primary, str) or not primary.strip():
            return None
        raw_fallbacks = model.get("fallbacks")
        fallbacks = (
            [f.strip() for f in raw_fallbacks if isinstance(f, str) and f.strip()]
            if isinstance(raw_fallbacks, list)
            else []
        )
        return (primary.strip(), fallbacks)
    return None


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

    async def default_model(self) -> dict[str, Any]:
        """``agents.defaults.model`` from the live gateway config.

        ``config.get`` returns the gateway's config snapshot (``path``,
        ``parsed``, ``resolved``, ``config`` = runtime-shaped, secrets
        redacted — see openclaw ``ConfigFileSnapshot``). The runtime-shaped
        ``config`` is preferred because it has ``$include`` / ``${ENV}``
        already applied; ``resolved`` / ``parsed`` are read when an older
        gateway omits it. When the RPC fails (gateway down, method unknown on
        an old build) ``~/.openclaw/openclaw.json`` is read instead, so the
        answer still reflects what the gateway will load next. Raises
        ``DefaultModelNotConfiguredError`` when neither names a model.
        """
        try:
            client = await self._default_client()
            resp = await client.send_request("config.get", {}, timeout=5)
            if resp.ok and isinstance(resp.payload, dict):
                payload = resp.payload
                for key in ("config", "resolved", "parsed"):
                    found = _default_model_from_config(payload.get(key))
                    if found is not None:
                        primary, fallbacks = found
                        log.debug(
                            "[default_model] %s from config.get (%s)", primary, key,
                        )
                        return {"model": primary, "fallbacks": fallbacks}
                raise DefaultModelNotConfiguredError(
                    f"{_DEFAULT_MODEL_KEY} is not set in the gateway config "
                    f"({payload.get('path') or 'config.get'})"
                )
            log.debug(
                "[default_model] config.get failed: %s",
                resp.error.message if resp.error else "no payload",
            )
        except DefaultModelNotConfiguredError:
            raise
        except Exception as e:  # noqa: BLE001 — fall through to the file
            log.debug("[default_model] config.get unavailable: %s", e)

        path = _openclaw_config_path()
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise DefaultModelNotConfiguredError(
                f"gateway unreachable and {path} does not exist"
            ) from e
        except (OSError, ValueError) as e:
            raise DefaultModelNotConfiguredError(
                f"gateway unreachable and {path} is unreadable: {e}"
            ) from e
        found = _default_model_from_config(config)
        if found is None:
            raise DefaultModelNotConfiguredError(
                f"{_DEFAULT_MODEL_KEY} is not set in {path}"
            )
        primary, fallbacks = found
        log.debug("[default_model] %s from %s", primary, path)
        return {"model": primary, "fallbacks": fallbacks}

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
