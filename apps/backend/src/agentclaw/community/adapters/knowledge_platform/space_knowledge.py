"""TC→ECB service adapter; never writes ECB tables or replaces failed KBs."""

from __future__ import annotations

from urllib.parse import quote, urlsplit

import httpx
from agentclaw.community.plugin_api.space_knowledge import (
    KnowledgeBasePage,
    KnowledgeBaseResult,
    KnowledgeBaseState,
    SpaceKnowledgeError,
    SyncedSpace,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator


class EcbSpaceKnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    enabled: bool = False
    base_url: str = ""
    base_url_pre: str = ""
    tc_env: str = ""
    timeout: float = Field(default=10.0, gt=0, le=60)

    @field_validator("base_url", "base_url_pre")
    @classmethod
    def validate_url(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("ECB URL must be an HTTPS service origin")
        return value.rstrip("/")

    @field_validator("tc_env")
    @classmethod
    def validate_env(cls, value: str) -> str:
        if value not in {"", "dev", "pre", "prod"}:
            raise ValueError(
                "tc_env must match the authoritative Space data environment"
            )
        return value


_ERRORS = {
    (403, "SPACE_MEMBER_REQUIRED"): "SPACE_MEMBER_REQUIRED",
    (403, "KB_PERMISSION_DENIED"): "KB_PERMISSION_DENIED",
    (404, "RESOURCE_NOT_FOUND"): "RESOURCE_NOT_FOUND",
    (409, "SPACE_NOT_SYNCED"): "SPACE_NOT_SYNCED",
    (409, "SPACE_REVISION_MISMATCH"): "SPACE_REVISION_MISMATCH",
    (409, "CREATE_REQUEST_CONFLICT"): "CREATE_REQUEST_CONFLICT",
    (410, "KB_DELETED"): "KB_DELETED",
    (400, "INVALID_TC_SPACE_INPUT"): "INVALID_INPUT",
}


def _identifier(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or len(value) > 128
        or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise SpaceKnowledgeError("INVALID_INPUT")
    return quote(value, safe="")


class EcbSpaceKnowledgeProvider:
    def __init__(self, config: EcbSpaceKnowledgeConfig, client: httpx.Client) -> None:
        self._config = config
        self._client = client

    def _request(
        self,
        method: str,
        space: SyncedSpace,
        actor_id: str,
        suffix: str,
        *,
        body: dict | None = None,
        query: dict | None = None,
    ) -> tuple[dict, int]:
        cfg = self._config
        if (
            not cfg.enabled
            or not cfg.base_url
            or not cfg.tc_env
            or space.environment != cfg.tc_env
        ):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")
        # actor_id is AuthPlugin's verified staffId, not the provider-specific SSO id.
        # Preserve its representation: stripping/padding would change ECB ownership.
        if len(actor_id) > 64:
            raise SpaceKnowledgeError("IDENTITY_REQUIRED")
        _identifier(actor_id)
        url = (
            cfg.base_url
            + "/api/v1/knowledge/integrations/tc/spaces/"
            + _identifier(space.space_id)
            + "/knowledge-bases"
            + suffix
        )
        try:
            response = getattr(self._client, method.lower())(
                url,
                **({"json": body} if method != "GET" else {}),
                params={"actor_staff_no": actor_id, **(query or {})}
                if method == "GET"
                else None,
                timeout=cfg.timeout,
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            # A create may already have committed. Caller must keep request_id.
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE") from None
        if response.status_code not in ({200} if method == "GET" else {200, 202}):
            detail = data.get("detail", {}) if isinstance(data, dict) else {}
            code = detail.get("code") if isinstance(detail, dict) else None
            mapped = (
                _ERRORS.get((response.status_code, code))
                if isinstance(code, str)
                else None
            )
            raise SpaceKnowledgeError(mapped or "DEPENDENCY_UNAVAILABLE")
        if not isinstance(data, dict):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")
        return data, response.status_code

    @staticmethod
    def _state(data: dict) -> KnowledgeBaseState:
        try:
            values = [
                data[k]
                for k in (
                    "kb_id",
                    "tenant_id",
                    "kb_name",
                    "description",
                    "kgbase_status",
                    "created_by",
                    "owner_staff_no",
                )
            ]
            if (
                not all(isinstance(value, str) for value in values)
                or any(not values[i] for i in (0, 1, 2, 5, 6))
                or values[4] not in {"creating", "ready", "failed"}
            ):
                raise ValueError("invalid provider response")
            state = KnowledgeBaseState(*values)
        except (KeyError, TypeError, ValueError):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE") from None
        # Explicit projection excludes raw last_error, ext, physical IDs and credentials.
        return state

    def _send(
        self,
        method: str,
        space: SyncedSpace,
        actor_id: str,
        suffix: str,
        *,
        body: dict | None = None,
    ) -> KnowledgeBaseResult:
        data, status = self._request(method, space, actor_id, suffix, body=body)
        return KnowledgeBaseResult(self._state(data), status == 202)

    def create(
        self,
        *,
        space: SyncedSpace,
        actor_id: str,
        request_id: str,
        name: str,
        description: str,
    ) -> KnowledgeBaseResult:
        _identifier(request_id)
        return self._send(
            "POST",
            space,
            actor_id,
            "",
            body={
                "request_id": request_id,
                "expected_space_revision": space.revision,
                "actor_staff_no": actor_id,
                "kb_name": name,
                "description": description,
            },
        )

    def get(
        self, *, space: SyncedSpace, actor_id: str, kb_id: str
    ) -> KnowledgeBaseResult:
        result = self._send("GET", space, actor_id, "/" + _identifier(kb_id))
        if result.state.kb_id != kb_id:
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")
        return result

    def list_knowledge_bases(
        self,
        *,
        space: SyncedSpace,
        actor_id: str,
        page_size: int = 50,
        cursor: str = "",
    ) -> KnowledgeBasePage:
        if not 1 <= page_size <= 100:
            raise SpaceKnowledgeError("INVALID_INPUT")
        if cursor:
            _identifier(cursor)
        data, _ = self._request(
            "GET", space, actor_id, "", query={"page_size": page_size, "cursor": cursor}
        )
        if data.get("space_id") != space.space_id or not isinstance(
            data.get("knowledge_bases"), list
        ):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")
        rows = data["knowledge_bases"]
        next_cursor = data.get("next_cursor")
        if (
            len(rows) > page_size
            or not all(isinstance(row, dict) for row in rows)
            or (
                next_cursor is not None
                and (not isinstance(next_cursor, str) or next_cursor <= cursor)
            )
        ):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")
        states = [self._state(row) for row in rows]
        if (
            len({state.kb_id for state in states}) != len(states)
            or any(state.kb_id <= cursor for state in states)
            or (
                next_cursor is not None
                and any(state.kb_id > next_cursor for state in states)
            )
        ):
            raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE")
        if next_cursor is not None:
            try:
                _identifier(next_cursor)
            except SpaceKnowledgeError:
                raise SpaceKnowledgeError("DEPENDENCY_UNAVAILABLE") from None
        return KnowledgeBasePage(states, next_cursor)
