"""BCN-backed Human-to-Bot admission reader."""
from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote

import httpx

from agentclaw.community.plugin_api.http_client import HttpClient


class FriendshipSourceUnavailableError(RuntimeError):
    """BCN could not make an authoritative friendship decision."""


class BcnHumanBotFriendshipService:
    """Use BCN's exact Human-to-Bot admission decision for chat authorization.

    ``allowed`` alone is insufficient: public-default admission is audience
    access, not the accepted edge required by friend chat.
    """

    def __init__(self, http_client: HttpClient, timeout: float = 10.0) -> None:
        self._http = http_client
        self._timeout = timeout

    def is_friend(
        self,
        *,
        human_id: str,
        bot_id: str,
        owner_id: str,
        request_headers: Mapping[str, str],
    ) -> bool:
        target_id = f"{bot_id}:{owner_id}"
        headers = {
            key: value
            for key, value in request_headers.items()
            if key.lower() in {"authorization", "cookie", "x-request-id", "x-trace-id"}
        }
        path = f"/bots/{quote(target_id, safe='')}/admission"
        try:
            response = self._http.get(
                path,
                params={"actor": human_id, "actor_kind": "human"},
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise FriendshipSourceUnavailableError(
                    "BCN admission response is not an object"
                )
            allowed = payload.get("allowed")
            reason = payload.get("reason_code")
            public_default = payload.get("public_default", False)
            if not isinstance(allowed, bool) or not isinstance(reason, str):
                raise FriendshipSourceUnavailableError(
                    "BCN admission response is incomplete"
                )
            return allowed and reason == "ok" and public_default is False
        except FriendshipSourceUnavailableError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise FriendshipSourceUnavailableError(
                "BCN friendship lookup unavailable"
            ) from error
