"""Bounded ECB management transport; runtime callbacks never call this adapter."""

from time import monotonic
from urllib.parse import quote, urlsplit

from agentclaw.community.adapters.knowledge_platform.config import (
    KnowledgeIntegrationConfig,
)
from agentclaw.community.kernel.knowledge import (
    BotKnowledgeScope,
    KnowledgeError,
    require_knowledge_id,
)
from agentclaw.community.kernel.knowledge_management import KnowledgeManagementError


class EcbKnowledgeClient:
    def __init__(
        self,
        origin: str,
        config: KnowledgeIntegrationConfig,
        *,
        client=None,
        allow_local_http: bool = False,
        local_http_hosts: frozenset[str] = frozenset({"127.0.0.1", "localhost"}),
    ):
        parsed = urlsplit(origin)
        if origin and (
            parsed.scheme != "https"
            and not (
                allow_local_http
                and parsed.scheme == "http"
                and parsed.hostname in local_http_hosts
            )
        ):
            raise ValueError("knowledge endpoint must be a trusted HTTPS origin")
        if origin and (
            not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("invalid knowledge origin")
        self.origin, self.config, self.client = origin.rstrip("/"), config, client

    def call(
        self,
        method: str,
        path: str,
        *,
        params=None,
        body=None,
        deadline: float | None = None,
    ) -> dict:
        from contextlib import nullcontext

        self.config.require_enabled()
        if not self.origin:
            raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503)
        try:
            remaining = deadline - monotonic() if deadline is not None else 3
            if remaining <= 0:
                raise ValueError("knowledge request deadline exceeded")
            if self.client is None:
                raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503)
            context = nullcontext(self.client)
            with context as client:
                with client.stream(
                    method,
                    self.origin + path,
                    params=params,
                    json=body,
                    headers={"Accept-Encoding": "identity"},
                    timeout=min(3, remaining),
                ) as response:
                    if response.status_code in {400, 403, 404, 409, 410}:
                        raise KnowledgeError(
                            "KNOWLEDGE_REMOTE_REJECTED", response.status_code
                        )
                    if (
                        response.status_code not in {200, 202}
                        or response.headers.get("content-encoding", "identity")
                        .strip()
                        .lower()
                        != "identity"
                    ):
                        raise ValueError("bad knowledge response")
                    data = bytearray()
                    for chunk in response.iter_bytes(chunk_size=4096):
                        if deadline is not None and monotonic() >= deadline:
                            raise ValueError("knowledge request deadline exceeded")
                        if len(data) + len(chunk) > 1024 * 1024:
                            raise ValueError("knowledge response too large")
                        data.extend(chunk)
            if deadline is not None and monotonic() >= deadline:
                raise ValueError("knowledge request deadline exceeded")
            import json

            payload = json.loads(data)
            if not isinstance(payload, dict):
                raise ValueError("invalid knowledge response")
            return payload
        except KnowledgeError:
            raise
        except Exception:
            raise KnowledgeError("KNOWLEDGE_DEPENDENCY_UNAVAILABLE", 503) from None

    @staticmethod
    def path(space_id: int, kb_id: str = "") -> str:
        path = f"/api/v1/knowledge/integrations/tc/spaces/{space_id}"
        return path + (f"/knowledge-bases/{quote(kb_id, safe='')}" if kb_id else "")


class RemoteKnowledgeCatalog:
    def __init__(
        self, remote: EcbKnowledgeClient, identities, platform: str = "teamclaw"
    ):
        self.remote, self.identities, self.platform = remote, identities, platform

    def list_in_space(
        self, *, space_id: int, actor_id: str, cursor: str = "", limit: int = 20
    ) -> dict:
        payload = self.remote.call(
            "GET",
            self.remote.path(space_id) + "/knowledge-bases",
            params={
                "actor_staff_no": self.identities.staff_no(actor_id),
                "cursor": cursor,
                "limit": limit,
            },
        )
        items = payload.get("items")
        if (
            not isinstance(items, list)
            or len(items) > limit
            or any(
                not isinstance(i, dict)
                or i.get("space_id") != str(space_id)
                or not isinstance(i.get("kb_id"), str)
                for i in items
            )
        ):
            raise KnowledgeError("KNOWLEDGE_SCOPE_MISMATCH", 503)
        cursor = payload.get("next_cursor")
        try:
            if cursor is not None:
                require_knowledge_id(cursor, "cursor")
            seen = set()
            for item in items:
                require_knowledge_id(item["kb_id"], "kb_id")
                if (
                    item["kb_id"] in seen
                    or not isinstance(item.get("name"), str)
                    or type(item.get("can_manage")) is not bool
                    or item.get("kgbase_status") not in {"ready", "creating", "failed"}
                ):
                    raise ValueError("invalid catalog item")
                seen.add(item["kb_id"])
        except ValueError:
            raise KnowledgeError("KNOWLEDGE_CATALOG_INVALID", 503) from None
        fields = (
            "space_id",
            "kb_id",
            "name",
            "kgbase_status",
            "can_manage",
            "description",
            "document_count",
            "authorized_bot_count",
            "created_at",
            "updated_at",
            "updated_at_basis",
        )
        return {
            "items": [
                {key: item[key] for key in fields if key in item} for item in items
            ],
            "next_cursor": cursor,
        }

    def _read(
        self,
        path: str,
        params: dict,
        *,
        deadline: float | None = None,
        method: str = "GET",
        body: dict | None = None,
    ) -> dict:
        try:
            return self.remote.call(
                method,
                path,
                params=params,
                **({"body": body} if body is not None else {}),
                **({"deadline": deadline} if deadline is not None else {}),
            )
        except KnowledgeError as exc:
            codes = {
                400: "KNOWLEDGE_REMOTE_INVALID_INPUT",
                403: "KNOWLEDGE_REMOTE_ACCESS_DENIED",
                404: "KNOWLEDGE_REMOTE_NOT_FOUND",
                409: "KNOWLEDGE_REMOTE_CONFLICT",
                410: "KNOWLEDGE_REMOTE_RETIRED",
            }
            code = (
                codes.get(exc.status, exc.code)
                if exc.code == "KNOWLEDGE_REMOTE_REJECTED"
                else exc.code
            )
            raise KnowledgeManagementError(code) from None

    def detail(self, *, space_id: int, kb_id: str, actor_id: str) -> dict:
        return self._read(
            self.remote.path(space_id, kb_id) + "/catalog-detail",
            {"actor_staff_no": self.identities.staff_no(actor_id)},
        )

    def details(self, *, space_id: int, kb_ids: list[str], actor_id: str) -> list[dict]:
        if len(kb_ids) > 20:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        if not kb_ids:
            return []
        try:
            for kb_id in kb_ids:
                require_knowledge_id(kb_id, "kb_id")
        except ValueError:
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT") from None
        if len(set(kb_ids)) != len(kb_ids):
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        # The remote batch checks every native ACL before returning any detail.
        # Never fall back to per-ID reads when the batch is rejected/unavailable.
        payload = self._read(
            self.remote.path(space_id) + "/knowledge-bases/catalog-details",
            {},
            method="POST",
            body={
                "actor_staff_no": self.identities.staff_no(actor_id),
                "kb_ids": kb_ids,
            },
            deadline=monotonic() + 8,
        )
        items = payload.get("items")
        if (
            payload.get("space_id") != str(space_id)
            or not isinstance(items, list)
            or len(items) != len(kb_ids)
            or any(
                not isinstance(item, dict)
                or item.get("kb_id") != kb_id
                or item.get("space_id") != str(space_id)
                for kb_id, item in zip(kb_ids, items, strict=True)
            )
        ):
            raise KnowledgeManagementError("KNOWLEDGE_CATALOG_INVALID")
        return items

    def authorized_bots(
        self, *, space_id: int, kb_id: str, actor_id: str, cursor: int, limit: int
    ) -> dict:
        return self._read(
            self.remote.path(space_id, kb_id) + "/authorized-bots",
            {
                "actor_staff_no": self.identities.staff_no(actor_id),
                "cursor": cursor,
                "limit": limit,
            },
        )

    def grant_states(self, *, kb_id, actor_id, identities, env):
        from agentclaw.community.adapters.knowledge_platform.grant_state_reader import (
            read_grant_states,
        )

        return read_grant_states(
            self, kb_id=kb_id, actor_id=actor_id, identities=identities, env=env
        )

    def page_authorized(self, *, scope, space_id: int, cursor: int, limit: int) -> dict:
        if (
            type(cursor) is not int
            or not 0 <= cursor <= 2**63 - 1
            or type(limit) is not int
            or not 1 <= limit <= 20
        ):
            raise KnowledgeManagementError("INVALID_KNOWLEDGE_INPUT")
        owner = self.identities.staff_no(scope.owner_id)
        payload = self._read(
            self.remote.path(space_id)
            + f"/bots/{quote(scope.bot_id, safe='')}/authorized-knowledge-bases",
            {"user_id": owner, "cursor": cursor, "limit": limit},
        )
        ids, next_cursor = payload.get("kb_ids"), payload.get("next_cursor")
        if (
            payload.get("bot_id") != scope.bot_id
            or payload.get("user_id") != owner
            or payload.get("space_id") != str(space_id)
            or not isinstance(ids, list)
            or len(ids) > limit
            or "next_cursor" not in payload
            or (
                next_cursor is not None
                and (
                    type(next_cursor) is not int
                    or not 1 <= next_cursor <= 2**63 - 1
                    or next_cursor == cursor
                )
            )
        ):
            raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID")
        try:
            for kb in ids:
                require_knowledge_id(kb, "kb_id")
            if ids != sorted(set(ids)):
                raise ValueError("unordered grant page")
        except (ValueError, TypeError):
            raise KnowledgeManagementError("KNOWLEDGE_GRANTS_INVALID") from None
        return {"kb_ids": ids, "next_cursor": next_cursor}

    def list_authorized(
        self, *, scope: BotKnowledgeScope, space_id: int
    ) -> tuple[str, ...]:
        owner = self.identities.staff_no(scope.owner_id)
        payload = self.remote.call(
            "GET",
            self.remote.path(space_id)
            + f"/bots/{quote(scope.bot_id, safe='')}/authorized-knowledge-bases",
            params={"user_id": owner},
        )
        ids = payload.get("kb_ids")
        if (
            payload.get("bot_id") != scope.bot_id
            or payload.get("user_id") != owner
            or payload.get("space_id") != str(space_id)
            or not isinstance(ids, list)
            or len(ids) != len(set(ids))
        ):
            raise KnowledgeError("KNOWLEDGE_SCOPE_MISMATCH", 503)
        for kb in ids:
            require_knowledge_id(kb, "kb_id")
        return tuple(ids)

    def set_grant(
        self, *, kb_id, actor_id, bot_id, owner_id, granted, expected_revision
    ):
        return self.remote.call(
            "PUT",
            f"/api/v1/knowledge/kb/{quote(kb_id, safe='')}/bot-authorizations/{quote(self.platform, safe='')}/{quote(bot_id, safe='')}",
            body={
                "actor_staff_no": self.identities.staff_no(actor_id),
                "user_id": self.identities.staff_no(owner_id),
                "granted": granted,
                "expected_revision": expected_revision,
            },
        )
