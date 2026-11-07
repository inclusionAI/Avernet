"""Opt-in live validation of best-effort Backend → BCS manager projection.

RUN_ACCEPTANCE=1 requires explicit MANAGER_SYNC_LIVE_* deployment configuration:
BACKEND_BASE_URL, BACKEND_PRINCIPAL, BACKEND_BUILD_SHA, BACKEND_ENV,
BACKEND_TENANT, BACKEND_BOT_ID, BACKEND_ENTITY_ID, BACKEND_BOT_PK,
BACKEND_OWNER_ID, SPACE_ID, BCS_BASE_URL, BCS_OWNER_PRINCIPAL,
BCS_DEPLOYED_VERSION, GATEWAY_SIGNING_KEY; BCS_HUMAN_INVITE_CODE if required.
Use only an isolated, ownership-aligned, registered Bot starting with no BCS
managers. Version values are recorded operator evidence, not remotely probed.
The fixture creates fresh test humans and mutates normal Backend HTTP routes;
BCS reads use a legal Owner principal, never the sync App's write credential.

Fault tests additionally require MANAGER_SYNC_LIVE_DESTRUCTIVE=1,
DESTRUCTIVE_UPSTREAM_URL and DESTRUCTIVE_PROXY_PORT (same prefix). The isolated
Backend's bcs_base_url must point to that test proxy. No fake/localhost default
is used when enabling this suite; do not configure shared production resources.
No Redis, marker clearing or restart safety guarantee is part of this contract.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import quote

import httpx
import jwt
import pytest

pytestmark = pytest.mark.acceptance


PRINCIPAL_LIFETIME_SECONDS = 300


class LiveCaseConfigError(RuntimeError):
    """The enabled live suite is missing explicit deployment configuration."""


def _env(name: str) -> str:
    import os

    return os.environ.get(name, "").strip()


@dataclass(frozen=True)
class LiveCaseConfig:
    """Every explicitly configured fact the live suite runs against."""

    backend_base_url: str
    backend_principal: str
    backend_build_sha: str
    backend_env: str
    backend_tenant: str
    backend_bot_id: str
    backend_entity_id: str
    backend_bot_pk: int
    backend_owner_id: str
    backend_space_id: int
    bcs_base_url: str
    bcs_owner_principal: str
    bcs_deployed_version: str
    gateway_signing_key: str
    bcs_human_invite_code: str
    destructive: bool
    destructive_upstream_url: str
    destructive_proxy_port: int

    @property
    def bcs_bot_target(self) -> str:
        """The stable BCS identity the sync addresses (survives handoff)."""
        return f"{self.backend_bot_id}:{self.backend_entity_id}"

    @staticmethod
    def resolve() -> "LiveCaseConfig":
        """Resolve the env contract — collecting *every* missing key.

        An absent key is a missing deployment fact, never a
        localhost/fake fallback: no defaults, no guesses.
        """
        base = {
            "MANAGER_SYNC_LIVE_BACKEND_BASE_URL": _env("MANAGER_SYNC_LIVE_BACKEND_BASE_URL"),
            "MANAGER_SYNC_LIVE_BACKEND_PRINCIPAL": _env("MANAGER_SYNC_LIVE_BACKEND_PRINCIPAL"),
            "MANAGER_SYNC_LIVE_BACKEND_BUILD_SHA": _env("MANAGER_SYNC_LIVE_BACKEND_BUILD_SHA"),
            "MANAGER_SYNC_LIVE_BACKEND_ENV": _env("MANAGER_SYNC_LIVE_BACKEND_ENV"),
            "MANAGER_SYNC_LIVE_BACKEND_TENANT": _env("MANAGER_SYNC_LIVE_BACKEND_TENANT"),
            "MANAGER_SYNC_LIVE_BACKEND_BOT_ID": _env("MANAGER_SYNC_LIVE_BACKEND_BOT_ID"),
            "MANAGER_SYNC_LIVE_BACKEND_ENTITY_ID": _env("MANAGER_SYNC_LIVE_BACKEND_ENTITY_ID"),
            "MANAGER_SYNC_LIVE_BACKEND_OWNER_ID": _env("MANAGER_SYNC_LIVE_BACKEND_OWNER_ID"),
            "MANAGER_SYNC_LIVE_BCS_BASE_URL": _env("MANAGER_SYNC_LIVE_BCS_BASE_URL"),
            "MANAGER_SYNC_LIVE_BCS_OWNER_PRINCIPAL": _env("MANAGER_SYNC_LIVE_BCS_OWNER_PRINCIPAL"),
            "MANAGER_SYNC_LIVE_BCS_DEPLOYED_VERSION": _env("MANAGER_SYNC_LIVE_BCS_DEPLOYED_VERSION"),
            "MANAGER_SYNC_LIVE_GATEWAY_SIGNING_KEY": _env("MANAGER_SYNC_LIVE_GATEWAY_SIGNING_KEY"),
        }
        numeric = {
            "MANAGER_SYNC_LIVE_BACKEND_BOT_PK": _env("MANAGER_SYNC_LIVE_BACKEND_BOT_PK"),
            "MANAGER_SYNC_LIVE_SPACE_ID": _env("MANAGER_SYNC_LIVE_SPACE_ID"),
        }
        missing = sorted(
            [name for name, value in base.items() if not value]
            + [name for name, value in numeric.items() if not value]
        )
        if missing:
            raise LiveCaseConfigError(
                "MANAGER_SYNC_LIVE_* configuration incomplete — this suite runs "
                "against an explicitly configured controlled Backend + "
                "deployed BCS and refuses localhost/fake fallbacks. Missing: "
                + ", ".join(missing)
                + ". Provide the deployment endpoints/credentials/version facts "
                "described in this module's docstring (secrets travel via the "
                "deployment secret channel, never in code)."
            )
        try:
            bot_pk = int(numeric["MANAGER_SYNC_LIVE_BACKEND_BOT_PK"])
            space_id = int(numeric["MANAGER_SYNC_LIVE_SPACE_ID"])
        except ValueError as exc:
            raise LiveCaseConfigError(
                "MANAGER_SYNC_LIVE_BACKEND_BOT_PK / MANAGER_SYNC_LIVE_SPACE_ID "
                "must be the numeric ids of the isolated case Bot/Space"
            ) from exc
        destructive = _env("MANAGER_SYNC_LIVE_DESTRUCTIVE") == "1"
        if destructive and (
            not _env("MANAGER_SYNC_LIVE_DESTRUCTIVE_UPSTREAM_URL")
            or not _env("MANAGER_SYNC_LIVE_DESTRUCTIVE_PROXY_PORT")
        ):
            raise LiveCaseConfigError(
                "MANAGER_SYNC_LIVE_DESTRUCTIVE=1 requires "
                "MANAGER_SYNC_LIVE_DESTRUCTIVE_UPSTREAM_URL, "
                "MANAGER_SYNC_LIVE_DESTRUCTIVE_PROXY_PORT — the destructive "
                "scenarios run only against an isolated fault-capable posture "
                "with the isolated Backend targeting the test proxy."
            )
        return LiveCaseConfig(
            backend_base_url=base["MANAGER_SYNC_LIVE_BACKEND_BASE_URL"],
            backend_principal=base["MANAGER_SYNC_LIVE_BACKEND_PRINCIPAL"],
            backend_build_sha=base["MANAGER_SYNC_LIVE_BACKEND_BUILD_SHA"],
            backend_env=base["MANAGER_SYNC_LIVE_BACKEND_ENV"],
            backend_tenant=base["MANAGER_SYNC_LIVE_BACKEND_TENANT"],
            backend_bot_id=base["MANAGER_SYNC_LIVE_BACKEND_BOT_ID"],
            backend_entity_id=base["MANAGER_SYNC_LIVE_BACKEND_ENTITY_ID"],
            backend_bot_pk=bot_pk,
            backend_owner_id=base["MANAGER_SYNC_LIVE_BACKEND_OWNER_ID"],
            backend_space_id=space_id,
            bcs_base_url=base["MANAGER_SYNC_LIVE_BCS_BASE_URL"],
            bcs_owner_principal=base["MANAGER_SYNC_LIVE_BCS_OWNER_PRINCIPAL"],
            bcs_deployed_version=base["MANAGER_SYNC_LIVE_BCS_DEPLOYED_VERSION"],
            gateway_signing_key=base["MANAGER_SYNC_LIVE_GATEWAY_SIGNING_KEY"],
            bcs_human_invite_code=_env("MANAGER_SYNC_LIVE_BCS_HUMAN_INVITE_CODE"),
            destructive=destructive,
            destructive_upstream_url=_env("MANAGER_SYNC_LIVE_DESTRUCTIVE_UPSTREAM_URL"),
            destructive_proxy_port=int(
                _env("MANAGER_SYNC_LIVE_DESTRUCTIVE_PROXY_PORT") or 0
            )
            or 0,
        )


def _mint_principal(
    *, key: str, audience: str, user_id: str, tenant: str | None = None,
) -> str:
    """Gateway-shaped principal JWT — the singlebox acceptance grammar."""
    if not key:
        raise LiveCaseConfigError(
            "MANAGER_SYNC_LIVE_GATEWAY_SIGNING_KEY is empty — principals for "
            "the fresh case humans cannot be minted; see the module docstring"
        )
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "gateway",
            "aud": audience,
            "iat": now,
            "exp": now + PRINCIPAL_LIFETIME_SECONDS,
            "principals": [
                {
                    "type": "user",
                    "tenant": tenant,
                    "subject": {"id": user_id, "username": f"{user_id}@example.test"},
                }
            ],
        },
        key,
        algorithm="HS256",
    )


class LiveCase:
    """One run's case: endpoints, clients, fresh humans, evidence, cleanup.

    Stable aliases ("admin", "member", …) carry this run's freshly created
    human ids; case humans are admitted on the BCS side only through its
    existing human interfaces (invite-codes/bind when the gate is on).
    """

    def __init__(self, config: LiveCaseConfig) -> None:
        self.config = config
        self.evidence: dict[str, Any] = {
            "backend_build_sha": config.backend_build_sha,
            "bcs_deployed_version": config.bcs_deployed_version,
            "env": config.backend_env,
            "avernet_tenant": config.backend_tenant,
            "bcs_bot_target": config.bcs_bot_target,
        }
        self._backend_client = httpx.Client(
            base_url=config.backend_base_url,
            headers={"X-Avernet-Principal": config.backend_principal},
            timeout=60.0,
        )
        self._owner_client = httpx.Client(
            base_url=config.bcs_base_url,
            headers={"x-avernet-principal": config.bcs_owner_principal},
            timeout=60.0,
        )
        self._aliases: dict[str, str] = {}
        self._collaborator_row_ids: dict[str, int] = {}
        self._baseline_manager_ids: set[str] | None = None
        #: A clean (faultless) proxy the destructive fixtures leave running for
        #: this case's own teardown sends; closed after teardown completes.
        self._cleanup_proxy: ThreadingHTTPServer | None = None

    # -- evidence/recording ------------------------------------------------

    def close(self) -> None:
        if self._cleanup_proxy is not None:
            self._cleanup_proxy.shutdown()
            self._cleanup_proxy.server_close()
            self._cleanup_proxy = None
        self._backend_client.close()
        self._owner_client.close()

    # -- the brief's helper interface ---------------------------------------

    def add_collaborator(self, user_id: str, role: str) -> dict[str, Any]:
        """Owner adds one case human as an explicit collaborator, by alias."""
        created_id = self._resolve_alias(user_id)
        response = self._backend_client.post(
            "/api/bot/collaborator/add",
            json={
                "bot_id": self.config.backend_bot_id,
                "owner_id": self.config.backend_owner_id,
                "user_id": created_id,
                "user_name": f"Live manager sync case {user_id}",
                "role": role,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True, body
        self._collaborator_row_ids[user_id] = body["data"]["id"]
        return body["data"]

    def remove_collaborator(self, user_id: str) -> dict[str, Any]:
        """Owner removes one case collaborator row, by alias."""
        row_id = self._require_row_id(user_id)
        response = self._backend_client.post(
            "/api/bot/collaborator/remove", json={"id": row_id},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True, body
        self._collaborator_row_ids.pop(user_id, None)
        return body.get("data")

    def update_collaborator(self, user_id: str, *, role: str) -> dict[str, Any]:
        """Owner updates one case collaborator's role, by alias."""
        row_id = self._require_row_id(user_id)
        response = self._backend_client.post(
            "/api/bot/collaborator/update", json={"id": row_id, "role": role},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True, body
        return body.get("data")

    def leave_collaboration(self, user_id: str) -> dict[str, Any]:
        """The case human leaves through their own principal (leave API)."""
        created_id = self._resolve_alias(user_id)
        response = httpx.post(
            f"{self.config.backend_base_url}/api/bot/collaborator/leave",
            headers={"X-Avernet-Principal": self._mint_backend_principal(created_id)},
            json={
                "bot_id": self.config.backend_bot_id,
                "owner_id": self.config.backend_owner_id,
            },
            timeout=60.0,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True, body
        self._collaborator_row_ids.pop(user_id, None)
        return body.get("data")

    def read_manager_ids(self) -> set[str]:
        """BCS's manager roster, projected into alias space.

        Reads through the human owner/manager GET (the only legitimate
        read the suite has), paging with the documented offset/limit
        grammar until ``total`` ids are collected; ids this run created
        are reported as their aliases, everything else passes through.
        """
        items: list[str] = []
        offset = 0
        total: int | None = None
        target = quote(self.config.bcs_bot_target, safe=":")
        while total is None or len(items) < total:
            response = self._owner_client.get(
                f"/api/v1/collaboration/bots/{target}/managers",
                params={"offset": offset, "limit": 100},
            )
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["code"] == 20000, body
            page = body["data"]
            page_items = page["items"]
            if not page_items:
                break
            items.extend(page_items)
            total = page["total"]
            offset += len(page_items)
        reverse = {user_id: alias for alias, user_id in self._aliases.items()}
        return {reverse.get(user_id, user_id) for user_id in items}

    def read_bcs_owner_user_id(self) -> str:
        """The BCS-side owner summary of the case target (ownership evidence)."""
        target = quote(self.config.bcs_bot_target, safe=":")
        response = self._owner_client.get(f"/api/v1/collaboration/bots/{target}/ownership")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["code"] == 20000, body
        return body["data"]["owner_user_id"]

    def wait_until(self, predicate: Callable[[], Any], *, timeout_seconds: int) -> None:
        """Deadline-bounded polling loop — exists only here, in tests."""
        deadline = time.monotonic() + timeout_seconds
        last_error: BaseException | None = None
        while time.monotonic() < deadline:
            try:
                if predicate():
                    return
            except Exception as exc:  # noqa: BLE001 — poll, retry until deadline
                last_error = exc
            time.sleep(1.0)
        detail = f" (last poll error: {last_error!r})" if last_error else ""
        raise AssertionError(f"condition not met within {timeout_seconds}s{detail}")

    # -- alias / human management -------------------------------------------

    def _resolve_alias(self, alias: str) -> str:
        if alias not in self._aliases:
            self._aliases[alias] = self._register_fresh_human(alias)
        return self._aliases[alias]

    def _require_row_id(self, alias: str) -> int:
        row_id = self._collaborator_row_ids.get(alias)
        assert row_id, f"alias {alias!r} has no live collaborator row to act on"
        return row_id

    def register_row_alias(self, alias: str) -> None:
        """Record a created-out-of-band collaborator row for cleanup (by user id)."""
        user_id = self._resolve_alias(alias)
        response = self._backend_client.get(
            "/api/bot/collaborator/list",
            params={
                "bot_id": self.config.backend_bot_id,
                "owner_id": self.config.backend_owner_id,
            },
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["success"] is True, body
        for row in body["data"]:
            if row["user_id"] == user_id:
                self._collaborator_row_ids[alias] = row["id"]
                return
        raise AssertionError(
            f"no collaborator row found on the Backend for alias {alias!r} "
            f"(user {user_id!r}) — the mutation under test did not commit"
        )

    def _register_fresh_human(self, alias: str) -> str:
        """Create + admit this run's human for an alias; never a shared identity."""
        user_id = f"mgrsync_live_{alias}_{uuid.uuid4().hex[:12]}"
        # Probe the human boundary with this run's principal; a 5xx means the
        # deployment cannot admit case humans through its own interfaces.
        probe = httpx.get(
            f"{self.config.bcs_base_url}/api/v1/collaboration/invite-codes/me",
            headers={"x-avernet-principal": self._mint_bcs_principal(user_id)},
            timeout=60.0,
        )
        assert probe.status_code < 500, (
            f"BCS invite-codes probe answered {probe.status_code}; the "
            "deployment must admit case humans through its own interfaces"
        )
        if self.config.bcs_human_invite_code:
            bind = httpx.post(
                f"{self.config.bcs_base_url}/openapi/v1/collaboration/invite-codes/bind",
                headers={"x-avernet-principal": self._mint_bcs_principal(user_id)},
                json={"code": self.config.bcs_human_invite_code},
                timeout=60.0,
            )
            assert bind.status_code < 500, (
                f"BCS invite-codes/bind answered {bind.status_code}; case "
                "humans cannot be admitted on this deployment"
            )
        return user_id

    def _mint_backend_principal(self, user_id: str) -> str:
        return _mint_principal(
            key=self.config.gateway_signing_key,
            audience="backend",
            user_id=user_id,
            tenant=self.config.backend_tenant,
        )

    def _mint_bcs_principal(self, user_id: str) -> str:
        return _mint_principal(
            key=self.config.gateway_signing_key,
            audience="bcs",
            user_id=user_id,
            tenant=self.config.backend_tenant,
        )

    # -- case lifecycle ------------------------------------------------------

    def record_baseline(self) -> None:
        """Assert the case-target preconditions and record the baseline roster.

        The cross-checks are the runbook §2 deployment preconditions that
        can be read through legitimate surfaces: the stable target answers
        on the BCS owner read, its registered owner matches the Backend
        owner, and the isolated case Bot starts with an *empty* roster
        (the empty-roster acceptance posture the core scenario needs).
        """
        self.evidence["bcs_owner_user_id"] = self.read_bcs_owner_user_id()
        assert self.evidence["bcs_owner_user_id"] == self.config.backend_owner_id, (
            "BCS ownership of the case target does not match the configured "
            "Backend owner — the owner-alignment precondition (runbook §2.6) "
            "is not satisfied; refusing to run against this target"
        )
        self._baseline_manager_ids = self.read_manager_ids()
        self.evidence["baseline_manager_count"] = len(self._baseline_manager_ids)
        assert not self._baseline_manager_ids, (
            "the isolated case Bot must start with an empty BCS manager "
            f"roster; found {sorted(self._baseline_manager_ids)!r}"
        )

    def teardown_to_baseline(self) -> None:
        """Cleanup through normal HTTP + wait for baseline convergence."""
        for alias in list(self._collaborator_row_ids):
            try:
                self.remove_collaborator(alias)
            except Exception:  # noqa: BLE001 — best-effort, reported below
                pass
        if self._baseline_manager_ids is not None:
            try:
                self.wait_until(
                    lambda: self.read_manager_ids() == self._baseline_manager_ids,
                    timeout_seconds=60,
                )
            except AssertionError as exc:
                # Not silently green: roster divergence after cleanup is real.
                raise AssertionError(
                    "live case teardown: BCS manager set did not return to the "
                    "recorded baseline within 60s after removing case rows"
                ) from exc


@pytest.fixture
def live_manager_case():
    """The brief's fixture: explicit endpoints, fresh humans, recorded evidence.

    Under ``RUN_ACCEPTANCE=1`` a missing deployment contract fails loudly —
    this suite never invents an endpoint or a credential.
    """
    try:
        config = LiveCaseConfig.resolve()
    except LiveCaseConfigError as exc:
        pytest.fail(f"live manager sync case is not runnable as enabled: {exc}")
    case = LiveCase(config)
    try:
        case.record_baseline()
        yield case
    finally:
        try:
            case.teardown_to_baseline()
        finally:
            case.close()


# ---------------------------------------------------------------------------
# The brief's core scenario (step 1) — body verbatim; the fixture's helper
# interface makes the aliases readable: "admin"/"member" live on BCS as the
# freshly registered ids this run created for them.
# ---------------------------------------------------------------------------


def test_admin_and_member_reach_bcs_and_removal_clears(live_manager_case):
    case = live_manager_case
    case.add_collaborator("admin", "admin")
    case.add_collaborator("member", "member")
    case.wait_until(lambda: case.read_manager_ids() == {"admin", "member"}, timeout_seconds=30)
    case.remove_collaborator("admin")
    case.remove_collaborator("member")
    case.wait_until(lambda: case.read_manager_ids() == set(), timeout_seconds=30)


# ---------------------------------------------------------------------------
# Extended acceptance matrix (brief step 3) — normal flows.
# ---------------------------------------------------------------------------


def test_update_and_leave_keep_bcs_in_step(live_manager_case):
    case = live_manager_case
    case.add_collaborator("admin", "admin")
    case.add_collaborator("member", "member")
    case.wait_until(lambda: case.read_manager_ids() == {"admin", "member"}, timeout_seconds=30)
    # admin ↔ member role swap keeps both in the BCS set (equal rights there).
    case.update_collaborator("member", role="admin")
    case.wait_until(lambda: case.read_manager_ids() == {"admin", "member"}, timeout_seconds=30)
    # Self-leave retracts the leaving human's BCS manager grant.
    case.leave_collaboration("member")
    case.wait_until(lambda: case.read_manager_ids() == {"admin"}, timeout_seconds=30)


def test_work_order_editor_approval_reaches_bcs(live_manager_case):
    case = live_manager_case
    applicant_id = case._resolve_alias("editor_applicant")
    # The applicant requests edit access through the existing work-order API…
    response = httpx.post(
        f"{case.config.backend_base_url}/openapi/v1/bots/{case.config.backend_bot_id}"
        f"/editor-requests",
        headers={"X-Avernet-Principal": case._mint_backend_principal(applicant_id)},
        json={"reason": "live manager sync acceptance: editor request"},
        timeout=60.0,
    )
    assert response.status_code == 201, response.text
    work_order_id = response.json()["data"]["work_order_id"]
    # …the owner approves it; the collaborator row that approval creates is
    # the mutation the side-channel must project without the approval caring.
    approval = case._backend_client.post(
        f"/openapi/v1/bots/work-orders/{work_order_id}/approval",
        json={"decision": "APPROVED"},
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["code"] == 200000, approval.text
    case.wait_until(
        lambda: "editor_applicant" in case.read_manager_ids(), timeout_seconds=30,
    )
    # The row was created out-of-band: register it so teardown uses the same
    # normal removal interface as every other case collaborator.
    case.register_row_alias("editor_applicant")


def test_space_member_removal_excludes_and_rejoin_restores(live_manager_case):
    case = live_manager_case
    case.add_collaborator("space_member", "member")
    case.wait_until(
        lambda: "space_member" in case.read_manager_ids(), timeout_seconds=30,
    )
    # Leaving the Space removes an explicit collaborator's operability: the
    # roster projection must follow, never widen, never clear the whole set.
    removed = httpx.post(
        f"{case.config.backend_base_url}/api/spaces/{case.config.backend_space_id}"
        f"/members/batch-remove",
        headers={"X-Avernet-Principal": case.config.backend_principal},
        json={"member_user_ids": [case._resolve_alias("space_member")]},
        timeout=60.0,
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["success"] is True, removed.text
    case.wait_until(
        lambda: "space_member" not in case.read_manager_ids(), timeout_seconds=30,
    )
    restored = httpx.post(
        f"{case.config.backend_base_url}/api/spaces/{case.config.backend_space_id}"
        f"/members/batch-add",
        headers={"X-Avernet-Principal": case.config.backend_principal},
        json={"member_user_ids": [case._resolve_alias("space_member")]},
        timeout=60.0,
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["success"] is True, restored.text
    case.wait_until(
        lambda: "space_member" in case.read_manager_ids(), timeout_seconds=30,
    )


# ---------------------------------------------------------------------------
# Fault injection stays strictly in the opt-in test boundary.

@dataclass
class _FaultPlan:
    reject_status: int | None = None
    disconnects_remaining: int = 0
    rewrite_total: int | None = None
    requests: list[dict[str, Any]] = field(default_factory=list)
    lock: Any = field(default_factory=threading.Lock)

    def count(self) -> int:
        with self.lock:
            return len(self.requests)


def _start_fault_proxy(config: LiveCaseConfig, plan: _FaultPlan) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _forward(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length else b""
            with plan.lock:
                plan.requests.append({"method": self.command, "path": self.path})
                reject = plan.reject_status
                disconnect = plan.disconnects_remaining > 0
                if disconnect:
                    plan.disconnects_remaining -= 1
                total = plan.rewrite_total
            if disconnect:
                # Request bytes accepted, response lost: the Backend cannot
                # infer whether the remote mutation happened. This proxy did
                # not forward it, allowing deterministic live retry evidence.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            if reject is not None:
                # A rejected request must not be forwarded and mutate BCS.
                self.send_response(reject)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            headers = {key: value for key, value in self.headers.items()
                       if key.lower() not in {"host", "content-length"}}
            with httpx.Client(base_url=config.destructive_upstream_url, timeout=30) as client:
                response = client.request(self.command, self.path, content=payload, headers=headers)
            body = response.content
            if total is not None:
                envelope = response.json()
                envelope["data"]["total"] = total
                body = json.dumps(envelope).encode()
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_PUT = _forward

        def log_message(self, *args: Any) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", config.destructive_proxy_port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def destructive_case(live_manager_case):
    if not live_manager_case.config.destructive:
        pytest.skip("fault tests require MANAGER_SYNC_LIVE_DESTRUCTIVE=1 and an isolated deployment")
    return live_manager_case


@pytest.fixture
def fault_proxy(destructive_case):
    plan = _FaultPlan()
    server = _start_fault_proxy(destructive_case.config, plan)
    try:
        yield plan
    finally:
        server.shutdown()
        server.server_close()
        destructive_case._cleanup_proxy = _start_fault_proxy(destructive_case.config, _FaultPlan())


def test_backend_success_with_bcs_403_keeps_business_result(destructive_case, fault_proxy):
    case = destructive_case
    fault_proxy.reject_status = 403
    row = case.add_collaborator("admin", "admin")
    assert row["role"] == "admin"
    case.wait_until(lambda: fault_proxy.count() >= 1, timeout_seconds=30)
    assert "admin" not in case.read_manager_ids()
    listing = case._backend_client.get("/api/bot/collaborator/list", params={
        "bot_id": case.config.backend_bot_id, "owner_id": case.config.backend_owner_id,
    })
    assert listing.status_code == 200 and listing.json()["success"] is True
    assert any(row["user_id"] == case._resolve_alias("admin") for row in listing.json()["data"])


def test_uncertain_delivery_retries_without_unlocking(destructive_case, fault_proxy):
    case = destructive_case
    fault_proxy.disconnects_remaining = 1
    assert case.add_collaborator("member", "member")["role"] == "member"
    case.wait_until(lambda: case.read_manager_ids() == {"member"}, timeout_seconds=30)
    assert fault_proxy.count() >= 2
    # No coordinator/marker recovery: a subsequent ordinary notification works.
    case.remove_collaborator("member")
    case.wait_until(lambda: not case.read_manager_ids(), timeout_seconds=30)


def test_reported_count_drift_does_not_roll_back_business(destructive_case, fault_proxy):
    case = destructive_case
    fault_proxy.rewrite_total = 0
    assert case.add_collaborator("member", "member")["role"] == "member"
    case.wait_until(lambda: fault_proxy.count() >= 1, timeout_seconds=30)
    # Rewriting the response cannot undo a replace BCS already applied.
    # The consumer's DRIFT classification is pinned by its contract suite.
    case.wait_until(lambda: "member" in case.read_manager_ids(), timeout_seconds=30)
