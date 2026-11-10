"""Shared-stack fixture operations and readback; never direct domain execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote
from uuid import uuid4

import httpx

from tests.community.acceptance.bot_collaborator.test_manager_sync_bcs_projection import (
    _BCS_BASE_URL, _PRINCIPAL_KEY, _REPO_ROOT, _add_collaborator,
    _assert_status, _ensure_human, _ensure_projection_target,
    _initialize_fixture_ownership, _owner_principal, _read_manager_ids, _wait_for_roster,
)
from tests.community.acceptance.bot_collaborator.test_member_management_live import (
    _execute_local_sql, _seed_member_managed_bot,
)
from tests.community.acceptance.bot_ownership_sync.fixture_passport import PREFIX


def fixture_staff_id(role, suffix):
    return f"{PREFIX}{role}{suffix}"


def maintenance_environment(database):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("COVERAGE_", "COV_CORE_"))}
    env.update({
        "DEPLOY_PROFILE": "singlebox", "SERVER_ENV": "dev",
        "SINGLEBOX_COVERAGE": "1", "SINGLEBOX_ACCEPTANCE_OWNER_HANDOFF": "1",
        "DATABASE_URL": f"sqlite:///{database}",
        "AGENTCLAW_SECRET_GATEWAY_PRINCIPAL_SIGNING_KEY_VALUE": _PRINCIPAL_KEY,
        "PYTHONPATH": f"{_REPO_ROOT / 'apps/backend/src'}:{_REPO_ROOT / 'apps/backend'}",
    })
    return env


class OwnershipCase:
    def __init__(self, backend_url):
        self.suffix = uuid4().hex[:12]
        self.bot_id = f"{PREFIX}bot_{self.suffix}"
        self.old_owner = fixture_staff_id("old", self.suffix)
        self.new_owner = fixture_staff_id("new", self.suffix)
        self.member = fixture_staff_id("member", self.suffix)
        self.entity_id = self.old_owner
        self.target = f"{self.bot_id}:{self.entity_id}"
        self.bot_pk = 0
        self.operation = ""
        self.bcs = httpx.Client(base_url=_BCS_BASE_URL, timeout=30)
        self.backend = httpx.Client(base_url=backend_url, headers={"x-user-id": self.old_owner}, timeout=60)
        self._log_path = _REPO_ROOT / "singlebox/.dependencies/logs/backend.log"
        self._log_offset = self._log_path.stat().st_size

    def sql(self, sql, **params):
        result = _execute_local_sql(self.backend, [{"sql": sql, "params": params}])
        return result["results"][0].get("rows") or []

    def seed(self, *, register_recipient=True, register_bot=True, transient=False):
        if transient:
            self.bot_id = f"{PREFIX}transient{self.suffix}"
            self.target = f"{self.bot_id}:{self.entity_id}"
        # UserService is real and the directory seam only recognises these
        # registered NORMAL/ACCESS records. No name fabricated by prefix alone.
        for user in (self.old_owner, self.new_owner, self.member):
            response = self.backend.post("/api/v1/user", json={
                "user_id": user, "user_type": "NORMAL", "status": "ACCESS",
            })
            _assert_status(response, 200, "Backend fixture user registration")
            assert response.json()["success"]
        for user in (self.old_owner, self.member):
            _ensure_human(self.bcs, user)
        if register_recipient:
            self.register_recipient()
        if register_bot:
            _ensure_projection_target(self.bcs, bcs_bot_id=self.target, owner_id=self.old_owner)
            _initialize_fixture_ownership(self.target)
        _seed_member_managed_bot(self.backend, owner_id=self.old_owner, bot_id=self.bot_id, active_engine="openclaw")
        # Initial fixture conversion ONLY. No SQL owner write after bootstrap.
        self.sql("UPDATE ac_bots SET bot_type='service' WHERE bot_id=:bot AND owner_id=:owner", bot=self.bot_id, owner=self.old_owner)
        rows = self.sql("SELECT id, entity_id, owner_id, bot_type FROM ac_bots WHERE bot_id=:bot", bot=self.bot_id)
        assert len(rows) == 1 and rows[0]["bot_type"] == "service"
        self.bot_pk = rows[0]["id"]
        assert rows[0]["entity_id"] == self.entity_id
        self.seed_runtime_binding()
        files = self.sql("SELECT file FROM pragma_database_list WHERE name='main'")
        assert len(files) == 1
        self.database = Path(files[0]["file"]).resolve()
        assert self.database.is_file() and self.database.is_relative_to((_REPO_ROOT / "singlebox/.dependencies").resolve())

    def seed_runtime_binding(self):
        # Bootstrap metadata for the explicitly controlled runtime boundary.
        # Keep the real preflight/repository reads enabled. This is not evidence
        # of a physical device or a real Passport update (see FixtureRuntime).
        device_id = f"{PREFIX}device_{self.suffix}"
        self.sql(
            "INSERT INTO ac_entity_device_binding "
            "(entity_id, entity_type, device_id, device_provider, env, device_props, "
            "status, apply_reason, applied_by, gmt_create, gmt_modified) "
            "VALUES (:entity, 'staff', :device, 'local', 'dev', '{}', 'ACTIVE', "
            "'ownership acceptance runtime metadata', :owner, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)",
            entity=self.entity_id, device=device_id, owner=self.old_owner,
        )
        self.sql(
            "UPDATE ac_bots SET binding_id=(SELECT id FROM ac_entity_device_binding "
            "WHERE device_id=:device AND entity_id=:entity AND env='dev'), "
            "device_id=:device WHERE id=:pk AND entity_id=:entity",
            device=device_id, entity=self.entity_id, pk=self.bot_pk,
        )

    def register_recipient(self):
        _ensure_human(self.bcs, self.new_owner)

    def add_collaborators(self):
        for user, role in ((self.new_owner, "admin"), (self.member, "member")):
            _add_collaborator(self.backend, bot_id=self.bot_id, owner_id=self.old_owner, user_id=user, role=role)

        # The collaborator UI's edit session owns a durable lock; close it
        # through its real HTTP command before starting a different operation.
        response = self.backend.post("/api/bot/collaborator/lock/release", json={
            "bot_id": self.bot_id, "owner_id": self.old_owner,
        })
        _assert_status(response, 200, "Backend collaborator edit-session release")
        assert response.json()["success"] and response.json()["data"]["released"]

    def handoff(self):
        response = self.backend.post("/api/bots/owner-handoffs", json={
            "bot_pk": self.bot_pk, "new_owner_id": self.new_owner,
            "request_key": self.suffix,
        })
        _assert_status(response, 200, "Backend handoff creation")
        assert response.json()["success"]
        self.operation = response.json()["data"]["operation_id"]
        deadline = time.monotonic() + 180
        last_phase = "not_observed"
        while time.monotonic() < deadline:
            response = self.backend.get(f"/api/bots/owner-handoffs/{self.operation}")
            _assert_status(response, 200, "Backend handoff status")
            row = response.json()["data"]
            last_phase = row["phase"]
            if last_phase == "SUCCEEDED":
                return
            time.sleep(0.5)
        raise AssertionError(f"handoff did not finish; phase={last_phase}")

    def assert_committed(self):
        rows = self.sql(
            "SELECT b.owner_id, b.entity_id, b.bot_id, h.phase, h.error_code "
            "FROM ac_bots b JOIN ac_bot_owner_handoff h ON h.bot_pk=b.id "
            "WHERE b.id=:pk AND h.operation_id=:operation",
            pk=self.bot_pk, operation=self.operation,
        )
        assert rows == [{"owner_id": self.new_owner, "entity_id": self.entity_id,
                         "bot_id": self.bot_id, "phase": "SUCCEEDED", "error_code": None}]

    def assert_queue_completed(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            rows = self.sql(
                "SELECT status, payload, attempts FROM ac_task_queue "
                "WHERE task_type='bot_owner_handoff' AND env='dev' "
                "AND json_extract(payload, '$.operation_id')=:operation",
                operation=self.operation,
            )
            if rows and all(row["status"] == "SUCCEEDED" for row in rows):
                assert sum(row["attempts"] for row in rows) >= 6
                assert all(json.loads(row["payload"]) == {
                    "operation_id": self.operation, "avernet_tenant": "teamclaw",
                } for row in rows)
                return
            time.sleep(0.1)
        raise AssertionError("real handoff TaskQueue did not reach SUCCEEDED")

    def read_owner(self, reader):
        response = self.bcs.get(f"/api/v1/collaboration/bots/{quote(self.target, safe='')}/ownership",
                                headers={"X-Avernet-Principal": _owner_principal(reader)})
        _assert_status(response, 200, "BCS ownership readback")
        body = response.json()
        assert body["code"] == 20000
        return body["data"]["owner_user_id"]

    def wait_owner(self, owner):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                if self.read_owner(owner) == owner:
                    return
            except AssertionError:
                pass
            time.sleep(0.25)
        raise AssertionError("BCS ownership did not converge")

    def wait_roster(self, owner, expected):
        _wait_for_roster(lambda: _read_manager_ids(self.bcs, bcs_bot_id=self.target, owner_id=owner), expected)

    def assert_old_owner_forbidden(self):
        for resource in ("ownership", "managers"):
            response = self.bcs.get(f"/api/v1/collaboration/bots/{quote(self.target, safe='')}/{resource}",
                                    headers={"X-Avernet-Principal": _owner_principal(self.old_owner)})
            _assert_status(response, 403, "BCS former-owner authority")

    def assert_bcs_bot_absent(self):
        response = self.bcs.get(f"/api/v1/collaboration/bots/{quote(self.target, safe='')}/ownership",
                                headers={"X-Avernet-Principal": _owner_principal(self.new_owner)})
        # Authority GET checks management permission before returning a summary;
        # an absent Bot grants nobody that permission (403, not 404).
        _assert_status(response, 403, "BCS absent Bot protected read")
        response = self.bcs.get(f"/bots/{quote(self.target, safe='')}", headers={
            "X-Mock-User-Id": self.new_owner, "X-Mock-Nick-Name": "acceptance absence read",
        })
        _assert_status(response, 404, "BCS registry absent Bot readback")
        assert response.json()["code"] == "bot_not_found"

    def business_event_count(self, event):
        with self._log_path.open("rb") as stream:
            stream.seek(self._log_offset)
            lines = stream.read().decode(errors="replace").splitlines()
        return sum(f"event={event} " in line and f" bot_pk={self.bot_pk} " in line
                   and " kind=ownership " in line for line in lines)

    def wait_business_event(self, event, *, after_count=0):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.business_event_count(event) > after_count:
                return
            time.sleep(0.25)
        raise AssertionError(f"business ownership diagnostic absent: {event}")

    def assert_one_proxy_drop_then_forward(self):
        with self._log_path.open("rb") as stream:
            stream.seek(self._log_offset)
            lines = stream.read().decode(errors="replace").splitlines()
        events = [line for line in lines if "owner_acceptance_proxy event=" in line
                  and f"bot_id={self.bot_id}" in line]
        assert sum("event=dropped " in line for line in events) == 1
        assert sum("event=forwarded " in line and "status=200" in line for line in events) >= 1
        assert "event=dropped " in events[0]

    def retry_terminal_handoff(self):
        response = self.backend.post(f"/api/bots/owner-handoffs/{self.operation}/retry")
        _assert_status(response, 200, "terminal handoff retry")
        assert response.json()["data"]["phase"] == "SUCCEEDED"

    def assert_no_new_business_confirmation(self, before):
        time.sleep(2)
        assert self.business_event_count("send_confirmed") == before

    def request_operations_repair(self, *, actor="100000", bot_pk=None):
        # Synthetic super_admin from application-singlebox.yaml. The normal
        # HTTP auth dependency and production operations service remain real.
        return self.backend.post(
            "/api/bots/owner-handoffs/bcn-repair",
            headers={"x-user-id": actor},
            json={"bot_pk": self.bot_pk if bot_pk is None else bot_pk},
        )

    def operations_repair(self):
        response = self.request_operations_repair()
        _assert_status(response, 202, "Backend ownership repair admission")
        body = response.json()
        assert body["success"]
        data = body["data"]
        assert data["bot_pk"] == self.bot_pk
        assert data["bcn_bot_id"] == self.target
        assert data["submission"] in {"queued", "coalesced"}
        assert data["delivery_confirmed"] is False
        return data

    def manual_repair(self):
        env = maintenance_environment(self.database)
        # Deliberately not coverage run: business runtime coverage belongs to the
        # server. The explicit operations Service API gets separately asserted.
        result = subprocess.run([
            sys.executable, "-m", "tests.community.acceptance.bot_ownership_sync.repair_driver",
            "--bot-pk", str(self.bot_pk), "--bot-id", self.bot_id, "--entity-id", self.entity_id,
        ], cwd=_REPO_ROOT / "apps/backend", env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, "manual repair driver failed (output withheld to protect credentials)"
        records = [line for line in result.stdout.splitlines() if line.startswith('{"evidence":')]
        assert len(records) == 1, "manual repair evidence is missing"
        evidence = json.loads(records[0])
        assert evidence["database_verified"] and evidence["drained"] and evidence["shutdown_clean"]
        assert evidence["evidence"] == "manual_repair"
        return evidence

    def close(self):
        # /bots/{id} DELETE is a leave command and explicitly forbids TC Bot
        # deletion. Do not misreport it as fixture cleanup. Unique rows stay in
        # the disposable singlebox runtime, never in source-controlled files.
        self.backend.close()
        self.bcs.close()
