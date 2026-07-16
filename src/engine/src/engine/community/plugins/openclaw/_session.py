"""_SessionPortMixin — session management port methods.

Includes session-only statics: _build_model_provider_map_from_payload,
_normalize_model_id, and _get_provider_map.
"""
from __future__ import annotations

import logging
from typing import Any

from engine.community.openclaw.client.gateway_client import OpenClawGatewayClient

log = logging.getLogger("openclaw-port")


class _SessionPortMixin:
    """Domain mixin: session CRUD + chat_history + model normalisation (pooled)."""

    # Session model-normalisation helpers (relocated from engines/openclaw/session.py)
    # Pure dict/string logic — no core types.

    @staticmethod
    def _build_model_provider_map_from_payload(
        providers_data: Any,
    ) -> dict[str, str]:
        """Build a model → provider mapping from a `providers.available` payload.

        Extracted from `OpenClawSessionService._build_model_provider_map` so the
        mapping is rebuilt per-call (no per-instance cache needed on the port —
        the provider catalogue is global config and doesn't vary per tenant).
        """
        model_provider_map: dict[str, str] = {}
        if isinstance(providers_data, dict):
            providers_list = providers_data.get("providers", [])
        elif isinstance(providers_data, list):
            providers_list = providers_data
        else:
            return model_provider_map

        for provider_info in providers_list:
            if not isinstance(provider_info, dict):
                continue
            provider_name = provider_info.get("id") or provider_info.get("name")
            if not provider_name:
                continue
            for model_info in provider_info.get("models", []):
                if isinstance(model_info, dict):
                    model_id = model_info.get("id") or model_info.get("name")
                elif isinstance(model_info, str):
                    model_id = model_info
                else:
                    continue
                if model_id:
                    pure_name = model_id.split("/")[-1] if "/" in model_id else model_id
                    model_provider_map[pure_name] = provider_name
                    model_provider_map[model_id] = provider_name
        return model_provider_map

    @staticmethod
    def _normalize_model_id(
        model_id: str | None,
        provider_map: dict[str, str] | None = None,
    ) -> str | None:
        """Normalise a model id to `provider/model` format.

        Relocated from `OpenClawSessionService._normalize_model_id`.
        """
        if not model_id:
            return None
        if "/" in model_id:
            return model_id
        if provider_map and model_id in provider_map:
            provider = provider_map[model_id]
            return f"{provider}/{model_id}"
        log.warning("[_normalize_model_id] no provider mapping for model %s", model_id)
        return f"antchat/{model_id}"

    async def _get_provider_map(self, client: OpenClawGatewayClient) -> dict[str, str]:
        """Return the cached model→provider map, building it on first call.

        `providers.available` is global config and does not vary per tenant,
        so we warm the map at most once per process lifetime (FIX 2).
        """
        if self._model_provider_map is not None:
            return self._model_provider_map

        provider_map: dict[str, str] = {}
        try:
            prov_resp = await client.send_request("providers.available", {})
            if prov_resp.ok and prov_resp.payload:
                provider_map = self._build_model_provider_map_from_payload(prov_resp.payload)
                log.info("[sessions_list] built provider map with %d entries", len(provider_map))
        except Exception as e:
            log.warning("[sessions_list] providers.available failed: %s", e)

        self._model_provider_map = provider_map
        return provider_map

    async def sessions_list(
        self,
        token: str | None = None,
        offset: int = 0,
        limit: int = 50,
        agent_id: str | None = None,
        session_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Orchestrate session listing with the exact legacy ordering.

        Exact sequence (mirrors `engines/openclaw/session.py:list`):
          1. `sessions.list` RPC → raw sessions
          2. Filter `bcs:group` sessions (gateway-cleanup, fixed)
          3. Filter by `agent_id` if provided
          4. Filter by exact non-blank `session_key` if provided
          5. Paginate: slice `[offset : offset+limit]` — BEFORE history fetch
          6. Fetch `chat.history` ONLY for the paginated page sessions
          7. Filter "Bot 初始化配置" single-message sessions from the page
          8. Normalise model strings (cached provider map)
          9. Return the final page dicts with `_messages`/`_message_count` set

        Returns `[]` on gateway error.  A page may return fewer than `limit`
        items when "Bot 初始化配置" sessions fall within it (exact legacy
        behaviour).
        """
        client = await self._pooled_client(token)

        # Step 1: sessions.list RPC
        try:
            response = await client.send_request("sessions.list", {})
        except ConnectionError as e:
            log.error("[sessions_list] connection failed: %s", e)
            return []
        except Exception as e:
            log.exception("[sessions_list] unexpected error: %s", e)
            return []

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error("[sessions_list] sessions.list failed: %s", error_msg)
            return []

        payload = response.payload or []
        if isinstance(payload, dict):
            raw_sessions: list[dict[str, Any]] = payload.get("sessions", [])
        elif isinstance(payload, list):
            raw_sessions = payload
        else:
            log.warning("[sessions_list] unexpected payload type: %s", type(payload).__name__)
            return []

        raw_sessions = [s for s in raw_sessions if isinstance(s, dict)]

        # Step 2: Filter bcs:group sessions (fixed gateway-cleanup).
        raw_sessions = [
            s for s in raw_sessions
            if (
                "bcs:group" not in s.get("key", "")
                or "bcs:group:bcs-cli" in s.get("key", "")
            )
        ]

        # Step 3: Filter by agent_id if provided (primitive, not DTO-driven).
        if agent_id is not None:
            raw_sessions = [s for s in raw_sessions if s.get("agentId") == agent_id]

        requested_session_key = session_key if session_key and session_key.strip() else None
        if requested_session_key is not None:
            # Filter before pagination so a copied key can be found beyond the current page.
            raw_sessions = [s for s in raw_sessions if s.get("key") == requested_session_key]

        # Step 5: Paginate BEFORE history fetch (legacy ordering).
        page = raw_sessions[offset: offset + limit]

        # Build/retrieve the cached provider map (FIX 2 — at most one RPC ever).
        provider_map = await self._get_provider_map(client)

        # Step 6: Fetch chat.history ONLY for the page sessions.
        # Step 7: Filter "Bot 初始化配置" single-message init sessions from page.
        enriched: list[dict[str, Any]] = []
        for s in page:
            key = s.get("key", "")
            messages: list[dict[str, Any]] = []
            try:
                hist_resp = await client.send_request(
                    "chat.history", {"sessionKey": key, "limit": 100}
                )
                if hist_resp.ok:
                    hist_payload = hist_resp.payload or []
                    if isinstance(hist_payload, dict):
                        messages = hist_payload.get("messages", [])
                    elif isinstance(hist_payload, list):
                        messages = hist_payload
            except Exception as e:
                log.warning("[sessions_list] chat.history failed for %s: %s", key, e)

            s["_message_count"] = len(messages)
            s["_messages"] = messages

            title = s.get("label") or s.get("displayName") or key
            if title and "Bot 初始化配置" in title and len(messages) == 1:
                continue

            enriched.append(s)

        # Step 8: Normalise model strings in place.
        # gateway 的 sessions.list 每行已经把 provider 拆到独立字段 modelProvider
        # （见 openclaw session-utils.ts resolveSessionDisplayModelIdentityRef:
        # 非 CLI provider 原样返回），model 字段是 bare id。优先用 row 上现成的
        # modelProvider 重建 provider/model，避免落到 _normalize_model_id 的
        # antchat/ 硬编码兜底（FIX: modelmng 等非 antchat provider 被误改写为 antchat）。
        for s in enriched:
            raw_model = s.get("model")
            raw_provider = s.get("modelProvider")
            if raw_model and "/" not in raw_model and raw_provider:
                s["_normalized_model"] = f"{raw_provider}/{raw_model}"
                log.info(
                    "[sessions_list] normalized via row modelProvider: key=%s %s/%s",
                    s.get("key", ""), raw_provider, raw_model,
                )
            else:
                s["_normalized_model"] = self._normalize_model_id(raw_model, provider_map)
                log.info(
                    "[sessions_list] normalized via provider_map fallback: key=%s "
                    "raw_model=%s raw_provider=%s result=%s",
                    s.get("key", ""), raw_model, raw_provider, s["_normalized_model"],
                )

        return enriched

    async def session_create(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `sessions.patch` to create a session; return the params dict used.

        Relocated from `engines/openclaw/session.py:create` up to the gateway
        call.  The adapter synthesises the Session DTO from the returned dict.
        Raises `RuntimeError` on gateway error.
        """
        client = await self._pooled_client(token)

        patch_params: dict[str, Any] = {"key": key}
        if label is not None:
            patch_params["label"] = label
        if model is not None:
            patch_params["model"] = model

        log.info("[session_create] sessions.patch params: %s", patch_params)
        response = await client.send_request("sessions.patch", patch_params, timeout=60.0)

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error("[session_create] sessions.patch failed: %s", error_msg)
            raise RuntimeError(f"Failed to create session: {error_msg}")

        log.info("[session_create] created session: %s", key)
        return patch_params

    async def session_delete(
        self,
        key: str,
        token: str | None = None,
    ) -> bool:
        """Call `sessions.delete`; return True on success, False on error.

        Relocated from `engines/openclaw/session.py:delete`.
        """
        client = await self._pooled_client(token)
        try:
            response = await client.send_request("sessions.delete", {"key": key})
        except ConnectionError as e:
            log.error("[session_delete] connection failed: %s", e)
            return False
        except Exception as e:
            log.exception("[session_delete] unexpected error: %s", e)
            return False

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error("[session_delete] sessions.delete failed: %s", error_msg)
            return False

        log.info("[session_delete] deleted session: %s", key)
        return True

    async def session_clear(
        self,
        key: str,
        token: str | None = None,
    ) -> None:
        """Call `sessions.reset` to clear a session's history.

        Relocated from `engines/openclaw/session.py:clear`.
        Raises `RuntimeError` on gateway error.
        """
        client = await self._pooled_client(token)
        response = await client.send_request("sessions.reset", {"key": key})

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error("[session_clear] sessions.reset failed: %s", error_msg)
            raise RuntimeError(f"Failed to clear session: {error_msg}")

        log.info("[session_clear] cleared session: %s", key)

    async def chat_history(
        self,
        session_key: str,
        limit: int | None = None,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Call `chat.history`; return raw message dicts.

        Relocated from `engines/openclaw/session.py:get_history` up to the
        raw-payload extraction; the dict→Message DTO build and offset/limit
        slicing moved to `core/adapters/openclaw/session.py`.
        Returns `[]` on gateway error.
        """
        client = await self._pooled_client(token)

        params: dict[str, Any] = {"sessionKey": session_key}
        if limit is not None:
            params["limit"] = limit

        try:
            response = await client.send_request("chat.history", params)
        except ConnectionError as e:
            log.error("[chat_history] connection failed: %s", e)
            return []
        except Exception as e:
            log.exception("[chat_history] unexpected error: %s", e)
            return []

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error("[chat_history] chat.history failed: %s", error_msg)
            return []

        payload = response.payload or []
        if isinstance(payload, dict):
            messages = payload.get("messages", [])
        elif isinstance(payload, list):
            messages = payload
        else:
            log.warning("[chat_history] unexpected payload type: %s", type(payload).__name__)
            return []

        log.info("[chat_history] session=%s returned %d messages", session_key, len(messages))
        return [m for m in messages if isinstance(m, dict)]

    async def session_patch_then_get(
        self,
        key: str,
        label: str | None = None,
        model: str | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Patch a session then find it via full-list scan.

        Relocated intact from `engines/openclaw/session.py:update` — the
        patch → sessions.list → find-by-id full scan is preserved.  Returns the
        raw session dict.

        Raises `RuntimeError` only when the **patch itself** fails.  When the
        patch succeeds but the session is not located in the rescan, legacy
        returned a best-effort synthetic session (the patch DID succeed, so the
        operation must report success); we mirror that by synthesising a minimal
        dict from the patch params — `_to_session` maps it to the same
        `Session` the legacy service built.
        """
        client = await self._pooled_client(token)

        patch_params: dict[str, Any] = {"key": key}
        if label is not None:
            patch_params["label"] = label
        if model is not None:
            patch_params["model"] = model

        log.info("[session_patch_then_get] sessions.patch params: %s", patch_params)
        response = await client.send_request("sessions.patch", patch_params)

        if not response.ok:
            error_msg = response.error.message if response.error else "Unknown error"
            log.error("[session_patch_then_get] sessions.patch failed: %s", error_msg)
            raise RuntimeError(f"Failed to update session: {error_msg}")

        log.info("[session_patch_then_get] patched session: %s; scanning list", key)

        # Full-list scan to get the updated session dict (relocate-intact from legacy).
        # Use a large limit so we cover all sessions in one page.
        all_sessions = await self.sessions_list(token=token, offset=0, limit=10000)
        for s in all_sessions:
            if s.get("key") == key:
                return s

        # Patch succeeded but the session is absent from the rescan — return a
        # best-effort synthetic dict from the patch params (legacy parity:
        # `engines/openclaw/session.py:update` returned a synthetic Session here
        # rather than raising, since the patch had already succeeded).
        log.warning(
            "[session_patch_then_get] session %s not found after patch; "
            "returning synthetic dict from patch params", key,
        )
        return {"key": key, "label": label, "model": model}

    async def session_reset(
        self,
        session_key: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Call `sessions.reset` via the gateway helper; return the in-band dict.

        Relocated from `engines/openclaw/session.py:reset`.
        Always returns a dict — never raises:
          {"success": True,  "payload": <dict>}
          {"success": False, "error":   {"code": ..., "message": ...}}
        """
        try:
            client = await self._pooled_client(token)
        except Exception as e:
            log.exception("[session_reset] connect failed: %s", e)
            return {
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": str(e)},
            }

        try:
            raw = await client.session_reset(session_key)
            return raw
        except Exception as e:
            log.exception("[session_reset] RPC failed: %s", e)
            return {
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": str(e)},
            }
