"""Shared-stack fixture operations and readback; never direct domain execution.

Every case owns one scope-uniqe Backend row and, when the story needs a target
on the delivery side, one BCS Bot registered through BCS's official admin
channels — the same fixture bootstrap the ownership/manager-sync suites use
(``POST /admin/bots/{bot_uuid}/ensure`` plus the ``bcs-admin bot-authority
initialize`` maintenance command). The Backend-side flows this module drives
(avatar edits, collaboration repair) go through the real owner-scoped Backend
routes; nothing here calls the ownership suite's enqueue-based
``repair_driver`` or any in-memory queue.

Ordinary public-local singlebox remains credentialless. The coverage runner
explicitly installs a test-only production plugin source backed by a provider
registered over real BCS HTTP and allowlisted in disposable runtime config.
The stories therefore exercise actual repair HTTP delivery without changing
public defaults, recording fake success, or calling domain logic directly.
"""
from __future__ import annotations

import json
import sqlite3
import time
from urllib.parse import quote
from uuid import uuid4

import httpx
import jwt

from tests.community.acceptance.bot_collaborator.test_manager_sync_bcs_projection import (
    _BCS_BASE_URL,
    _BCS_CONFIG_DIR,
    _PRINCIPAL_KEY,
    _REPO_ROOT,
    _assert_status,
    _ensure_human,
    _ensure_projection_target,
    _initialize_fixture_ownership,
    _owner_principal,
    _read_manager_ids,
)
from tests.community.acceptance.bot_collaborator.test_member_management_live import (
    _execute_local_sql,
    _seed_member_managed_bot,
)

PREFIX = "colabrepacc"

_BCS_DEPENDENCIES_ROOT = (_REPO_ROOT / "singlebox/.dependencies").resolve()

def _backend_principal_headers(user_id: str) -> dict[str, str]:
    """Gateway-signed principal for the public API — what the backend verifies.

    Same shape the other public-API acceptance stories mint (aud=backend, the
    shared singlebox signing key the coverage runner and the backend agree on).
    """
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": "gateway",
            "aud": "backend",
            "iat": now,
            "exp": now + 300,
            "principals": [
                {
                    "type": "user",
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        _PRINCIPAL_KEY,
        algorithm="HS256",
    )
    return {"X-Avernet-Principal": token}


def _bcs_database_path() -> str:
    """The live BCS sqlite the stack owns; read-only access from the story."""
    dependencies = _BCS_CONFIG_DIR.resolve().parent
    database = dependencies / "bcs_data" / "bcs.db"
    assert database.is_relative_to(_BCS_DEPENDENCIES_ROOT), (
        "BCS fixture database must live inside the disposable singlebox runtime"
    )
    assert database.is_file(), "BCS fixture database is missing for the live stack"
    return str(database)


class RepairCase:
    """One story's scope: its own users, its own Backend row, its own BCS Bot."""

    def __init__(self, backend_url: str):
        self.suffix = uuid4().hex[:12]
        self.bot_id = f"{PREFIX}bot_{self.suffix}"
        self.owner = f"{PREFIX}own{self.suffix}"
        self.other_user = f"{PREFIX}oth{self.suffix}"
        self.entity_id = self.owner
        self.target = f"{self.bot_id}:{self.entity_id}"
        self.bot_pk = 0
        self.group_id = ""
        self.session_id = ""
        self.bcs = httpx.Client(base_url=_BCS_BASE_URL, timeout=30)
        self.backend = httpx.Client(
            base_url=backend_url, headers={"x-user-id": self.owner}, timeout=60
        )
        self._log_path = _REPO_ROOT / "singlebox/.dependencies/logs/backend.log"
        self._log_offset = self._log_path.stat().st_size

    # ------------------------------------------------------------------
    # Seeding (Backend row via SQL helper; BCS Bot via official admin channels)
    # ------------------------------------------------------------------

    def sql(self, sql: str, **params):
        result = _execute_local_sql(self.backend, [{"sql": sql, "params": params}])
        return result["results"][0].get("rows") or []

    def seed(
        self,
        *,
        register_bcs: bool = True,
        register_avatar_url: str | None = None,
    ):
        self._register_backend_user(self.owner)
        self._register_backend_user(self.other_user)
        _ensure_human(self.bcs, self.owner)
        if register_bcs:
            _ensure_projection_target(self.bcs, bcs_bot_id=self.target, owner_id=self.owner)
            _initialize_fixture_ownership(self.target)
            if register_avatar_url is not None:
                self.admin_onboard(
                    name=f"Story bot {self.bot_id}",
                    summary="collaboration repair acceptance fixture",
                    avatar_url=register_avatar_url,
                )
        _seed_member_managed_bot(
            self.backend, owner_id=self.owner, bot_id=self.bot_id, active_engine="openclaw"
        )
        rows = self.sql(
            "SELECT id, entity_id, owner_id FROM ac_bots WHERE bot_id=:bot", bot=self.bot_id
        )
        assert len(rows) == 1 and rows[0]["owner_id"] == self.owner
        self.bot_pk = rows[0]["id"]
        assert rows[0]["entity_id"] == self.entity_id
        files = self.sql("SELECT file FROM pragma_database_list WHERE name='main'")
        assert len(files) == 1

    def save_profile(self, *, name: str, summary: str, avatar_url: str) -> None:
        """Fixture-only saved state; repair itself must deliver it over HTTP."""
        self.sql(
            "UPDATE ac_bots SET bot_name=:name, bot_desc=:summary, ext=:ext WHERE id=:pk",
            name=name, summary=summary, ext=json.dumps({"avatar_url": avatar_url}),
            pk=self.bot_pk,
        )

    def save_unreadable_avatar(self) -> None:
        """An invalid saved value must refuse repair, never become a clear."""
        self.sql(
            "UPDATE ac_bots SET ext=:ext WHERE id=:pk",
            ext=json.dumps({"avatar_url": 42}), pk=self.bot_pk,
        )

    def save_manager(self, user_id: str) -> None:
        """Seed one saved collaborator without running background sync."""
        _ensure_human(self.bcs, user_id)
        self.sql(
            "INSERT INTO ac_bot_collaborator "
            "(bot_pk, bot_id, owner_id, user_id, role, operator_id, env, gmt_create, gmt_modified) "
            "VALUES (:pk, :bot, :owner, :user, 'member', :owner, 'dev', "
            "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            pk=self.bot_pk, bot=self.bot_id, owner=self.owner, user=user_id,
        )

    def save_caller(self, *, enabled: bool = True, revision: int = 1) -> None:
        """Seed an established caller-config fact without bumping its revision."""
        call_type = "caller" if enabled else "owner"
        self.sql(
            "UPDATE ac_bots SET call_type=:call_type, caller_config_revision=:revision WHERE id=:pk",
            call_type=call_type, revision=revision, pk=self.bot_pk,
        )

    def _register_backend_user(self, user_id: str) -> None:
        response = self.backend.post(
            "/api/v1/user",
            json={"user_id": user_id, "user_type": "NORMAL", "status": "ACCESS"},
        )
        _assert_status(response, 200, "Backend fixture user registration")
        assert response.json()["success"]


    # ------------------------------------------------------------------
    # BCS official admin/profile channel (registration carry + drift facility)
    # ------------------------------------------------------------------

    def admin_onboard(self, *, name: str, summary: str = "", avatar_url: str | None = None):
        """Real ``POST /admin/bots/onboard`` — the profile channel the Backend's
        daily sync speaks; fixture-owned target only. Carries an explicit
        avatar (``""`` clears) exactly like the T8 replay steps."""
        payload: dict[str, object] = {
            "bot_id": self.target,
            "name": name,
            "summary": summary,
        }
        if avatar_url is not None:
            payload["avatar_url"] = avatar_url
        response = self.bcs.post(
            "/admin/bots/onboard",
            json=payload,
            headers={
                "X-Mock-User-Id": self.owner,
                "X-Mock-Nick-Name": "collaboration repair acceptance",
            },
        )
        _assert_status(response, 200, "BCS admin onboard")
        body = response.json()
        assert body.get("onboarded") is True, body
        assert body.get("bot_uuid") == self.target, body
        return body

    # ------------------------------------------------------------------
    # Authoritative readback on the delivery side
    # ------------------------------------------------------------------

    def _bcs_query(self, sql: str, params: tuple = ()):
        connection = sqlite3.connect(f"file:{_bcs_database_path()}?mode=ro", uri=True)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def registry_row(self) -> dict[str, object | None]:
        rows = self._bcs_query(
            "SELECT name, avatar_url FROM bcs_bots WHERE bot_uuid=?", (self.target,)
        )
        assert len(rows) == 1, "expected exactly one BCS row for the fixture Bot"
        return {"name": rows[0][0], "avatar_url": rows[0][1]}

    def read_bcs_summary(self) -> str | None:
        rows = self._bcs_query(
            "SELECT bot_info FROM bcs_bots WHERE bot_uuid=?", (self.target,)
        )
        assert len(rows) == 1, "expected exactly one BCS row for the fixture Bot"
        return json.loads(rows[0][0])["summary"]

    def bcs_bot_count(self) -> tuple[int, int]:
        total = self._bcs_query("SELECT COUNT(*) FROM bcs_bots")[0][0]
        target = self._bcs_query(
            "SELECT COUNT(*) FROM bcs_bots WHERE bot_uuid=?", (self.target,)
        )[0][0]
        return int(total), int(target)

    def read_bcs_owner(self) -> str:
        response = self.bcs.get(
            f"/api/v1/collaboration/bots/{quote(self.target, safe='')}/ownership",
            headers={"X-Avernet-Principal": _owner_principal(self.owner)},
        )
        _assert_status(response, 200, "BCS ownership readback")
        body = response.json()
        assert body["code"] == 20000
        return body["data"]["owner_user_id"]

    def read_bcs_managers(self) -> set[str]:
        return _read_manager_ids(self.bcs, bcs_bot_id=self.target, owner_id=self.owner)

    def assert_bcs_bot_absent(self) -> None:
        response = self.bcs.get(
            f"/bots/{quote(self.target, safe='')}",
            headers={
                "X-Mock-User-Id": self.owner,
                "X-Mock-Nick-Name": "acceptance absence read",
            },
        )
        _assert_status(response, 404, "BCS registry absent Bot readback")
        assert response.json()["code"] == "bot_not_found"

    # ------------------------------------------------------------------
    # Backend owner-scoped routes (avatar edit + the manual repair interface)
    # ------------------------------------------------------------------

    def put_avatar(self, avatar_url: str) -> None:
        """Real owner avatar edit; ``""`` is the explicit clear. Syncs to BCS."""
        response = self.backend.put(
            f"/openapi/v1/bots/{self.bot_id}/avatar",
            params={"user_id": self.owner},
            json={"avatar_url": avatar_url},
            headers=_backend_principal_headers(self.owner),
        )
        _assert_status(response, 200, "Backend avatar edit")
        body = response.json()
        assert body["data"]["avatar_url"] == avatar_url

    def repair(self, *, as_user: str | None = None) -> httpx.Response:
        user = as_user or self.owner
        response = self.backend.post(
            f"/openapi/v1/bots/{self.bot_id}/collaboration/repair",
            params={"user_id": user},
            headers=_backend_principal_headers(user),
        )
        assert response.headers.get("content-type", "").startswith("application/json")
        return response

    # ------------------------------------------------------------------
    # BCS V1 Group/Session readback (current-avatar authoritative views)
    # ------------------------------------------------------------------

    def create_group_and_session(self) -> None:
        response = self.bcs.post(
            "/openapi/v1/collaboration/groups",
            json={
                "group_kind": "normal",
                "name": f"Colab repair acceptance {self.suffix}",
                "driver_bot_uuid": self.target,
                "participants": [],
                "collaboration": {"strategy": "chat"},
            },
            headers={"X-Avernet-Principal": _owner_principal(self.owner)},
        )
        _assert_status(response, 201, "BCS V1 group create")
        self.group_id = response.json()["data"]["group_id"]
        response = self.bcs.post(
            f"/openapi/v1/collaboration/groups/{self.group_id}/sessions",
            json={"kind": "chat"},
            headers={"X-Avernet-Principal": _owner_principal(self.owner)},
        )
        _assert_status(response, 201, "BCS V1 session create")
        self.session_id = response.json()["data"]["session_id"]

    def _participant_avatar(self, participants: list) -> object:
        rows = [p for p in participants if p.get("actor_id") == self.target]
        assert len(rows) == 1, "expected the fixture Bot in the participant list"
        assert "avatar_url" in rows[0], "avatar_url must stay an explicit field"
        return rows[0]["avatar_url"]

    def v1_group_list_avatar(self) -> object:
        # view_bot_id selects the Driver Bot (created_by == the authenticated
        # owner) as the view actor — the group list is membership-scoped and
        # the owner human is not a member of the driver-only group.
        response = self.bcs.get(
            "/openapi/v1/collaboration/groups",
            params={"limit": 50, "view_bot_id": self.target},
            headers={"X-Avernet-Principal": _owner_principal(self.owner)},
        )
        _assert_status(response, 200, "BCS V1 group list readback")
        items = [
            item
            for item in response.json()["data"]["items"]
            if item["group_id"] == self.group_id
        ]
        assert len(items) == 1
        return self._participant_avatar(items[0]["participants"])

    def v1_group_detail_avatar(self) -> object:
        response = self.bcs.get(
            f"/openapi/v1/collaboration/groups/{self.group_id}",
            headers={"X-Avernet-Principal": _owner_principal(self.owner)},
        )
        _assert_status(response, 200, "BCS V1 group detail readback")
        return self._participant_avatar(response.json()["data"]["participants"])

    def v1_session_list_avatar(self) -> object:
        response = self.bcs.get(
            f"/openapi/v1/collaboration/groups/{self.group_id}/sessions",
            params={"limit": 20, "view_bot_id": self.target},
            headers={"X-Avernet-Principal": _owner_principal(self.owner)},
        )
        _assert_status(response, 200, "BCS V1 session list readback")
        items = [
            item
            for item in response.json()["data"]["items"]
            if item["session_id"] == self.session_id
        ]
        assert len(items) == 1
        return self._participant_avatar(items[0]["participants"])

    def v1_session_detail_avatar(self) -> object:
        response = self.bcs.get(
            f"/openapi/v1/collaboration/sessions/{self.session_id}",
            headers={"X-Avernet-Principal": _owner_principal(self.owner)},
        )
        _assert_status(response, 200, "BCS V1 session detail readback")
        return self._participant_avatar(response.json()["data"]["participants"])

    # ------------------------------------------------------------------
    # Log discipline for the repair route
    # ------------------------------------------------------------------

    def log_lines(self) -> list[str]:
        with self._log_path.open("rb") as stream:
            stream.seek(self._log_offset)
            return stream.read().decode(errors="replace").splitlines()

    def assert_no_repair_queue(self) -> None:
        rows = self.sql(
            "SELECT COUNT(*) FROM ac_task_queue WHERE task_type='collaboration_repair'"
        )
        assert rows[0]["COUNT(*)"] == 0, "repair never enqueues background work"

    def close(self) -> None:
        # Unique fixture rows stay in the disposable singlebox runtime; nothing
        # outside this case's own scope was ever mutated.
        self.backend.close()
        self.bcs.close()
