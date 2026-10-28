"""Live acceptance: Backend→BCS manager sync over a controlled release Backend + a deployed dev BCS.

**Evidence separation (binding):** this suite is the *external live
evidence* for the manager-sync feature; it is not part of the release
singlebox coverage gate. The default singlebox builds the release tree's
own BCS, which does not implement ``PUT /bots/{bot_id}/managers`` — its
gates say nothing about this feature, and this directory is deliberately
not registered in ``singlebox/ci/singlebox_coverage_modules.yaml`` (the
Backend's existing singlebox gates stay the authority for everything the
feature must not have changed).

**What runs here, and what never does:** mutations are driven through the
release Backend's *normal* HTTP interfaces only, and every effect is
verified by reading the deployed dev BCS through an *owner/manager human
credential* on the existing ``GET /api/v1/collaboration/bots/{bot_id}``
authority surface. The trusted App that ``PUT``s the sync is configured
inside the deployment (``bot_manager_sync`` block); this suite never holds
an App credential and never assumes an App may ``GET`` managers — an
ordinary App has no such scope, so a 403/404 from it is never a roster
answer.

**Identity model:** ``"admin"``/``"member"``/… in the tests are *stable
aliases*. For each run the fixture creates a fresh human id per alias
(``mgrsync_live_<alias>_<random>``) — the code under test never sees a
shared employee identity — and :meth:`LiveCase.read_manager_ids` reports
the observed BCS roster *projected back into alias space*, so the brief's
scenario reads exactly like its contract; ids this run did not create
pass through unprojected.

**Configuration contract — every value is explicit, none invented:** with
``RUN_ACCEPTANCE=1`` this suite either runs against a fully configured
controlled environment or fails loudly naming every missing key — it
never falls back to localhost, in-singlebox fakes, or invented
credentials. Without ``RUN_ACCEPTANCE=1`` the repo-wide acceptance
conftest skips this suite (recorded as NOT RUN). Configuration comes from
the environment (prefix ``MANAGER_SYNC_LIVE_``); secrets are never
printed and never written to artifacts.

Required (base posture — every matrix test); suffixes below attach to
``MANAGER_SYNC_LIVE_``:

- ``BACKEND_BASE_URL`` — controlled release Backend origin;
  ``BACKEND_PRINCIPAL`` — gateway-signed ``X-Avernet-Principal`` of the
  case Bot's *owner* (secret); ``BACKEND_BUILD_SHA`` — the Backend build
  actually deployed there (release evidence, recorded not probed).
- ``BACKEND_ENV`` / ``BACKEND_TENANT`` — the deployment's env label and
  the *isolated test tenant* the case resources live in.
- ``BACKEND_BOT_ID`` / ``_ENTITY_ID`` / ``_BOT_PK`` / ``_OWNER_ID`` —
  the **isolated case Bot**: fresh/dedicated, member-management enabled,
  Team Space attached, whose BCS manager roster starts *empty* (the
  empty-roster acceptance posture), registered on the dev BCS under the
  stable identity ``<bot_id>:<entity_id>``, cross-checked through the
  owner read before anything runs (runbook §2 registration evidence).
- ``SPACE_ID`` — the case Bot's Team Space (space matrix).
- ``BCS_BASE_URL`` — deployed dev BCS origin; ``BCS_OWNER_PRINCIPAL`` —
  the registered Owner human's ``x-avernet-principal`` (secret) used for
  the evidence reads; ``BCS_DEPLOYED_VERSION`` — the BCS deployment
  actually running there (recorded; the dev-source Rust tests prove the
  contract, never the deployment).
- ``GATEWAY_SIGNING_KEY`` — the deployment's controlled *test* gateway
  principal signing key (secret); the fixture mints the fresh case
  humans' short-lived principals, like the singlebox acceptance does.
- ``BCS_HUMAN_INVITE_CODE`` — optional; required when the deployed BCS
  runs the invite-code gate (fresh humans via ``invite-codes/bind``).

Destructive posture, opt-in via ``MANAGER_SYNC_LIVE_DESTRUCTIVE=1`` (else
every destructive test skips: scenario NOT covered, never a silent green):
``DESTRUCTIVE_UPSTREAM_URL`` (BCS origin the in-test fault proxy
forwards to, with the deployment's ``bot_manager_sync.bcs_base_url``
pointed at the proxy),
``DESTRUCTIVE_PROXY_PORT`` (local proxy port), and ``MAINTENANCE_PROFILE``
(DeployProfile for the deployment maintenance injector, runbook §1 — the
drain/compensation legs work only inside that environment).

Isolation and cleanup: the case Bot belongs to the isolated test tenant;
the fixture asserts its BCS roster starts empty (baseline), and on
teardown removes every added collaborator through the same normal HTTP
interfaces and waits for the baseline. The one deliberate exception is
the owner-check drill — it stays last in the file and leaves the target
blocked: re-running this suite against the same target first needs the
runbook §5.3 owner reconciliation plus target re-provisioning.
"""

from __future__ import annotations

import json
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
    maintenance_profile: str

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
                "against an explicitly configured controlled release Backend + "
                "deployed dev BCS and refuses localhost/fake fallbacks. Missing: "
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
            or not _env("MANAGER_SYNC_LIVE_MAINTENANCE_PROFILE")
        ):
            raise LiveCaseConfigError(
                "MANAGER_SYNC_LIVE_DESTRUCTIVE=1 requires "
                "MANAGER_SYNC_LIVE_DESTRUCTIVE_UPSTREAM_URL, "
                "MANAGER_SYNC_LIVE_DESTRUCTIVE_PROXY_PORT and "
                "MANAGER_SYNC_LIVE_MAINTENANCE_PROFILE — the destructive "
                "scenarios run only against an isolated fault-capable posture "
                "with the maintenance environment available (runbook §1/§4)."
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
            maintenance_profile=_env("MANAGER_SYNC_LIVE_MAINTENANCE_PROFILE"),
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
        #: Set by the owner-check drill: teardown's roster wait is intentionally
        #: skipped because the target is left blocked pending ops §5.3.
        self._teardown_note: str | None = None
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
        if self._teardown_note:
            # Deliberate, recorded exception (owner-check drill): the target is
            # left blocked pending the runbook §5.3 ops reconciliation.
            return
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
        case.teardown_to_baseline()
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
# Extended matrix (brief step 3) — destructive/fault scenarios.
#
# These run only against an isolated fault-capable posture:
# MANAGER_SYNC_LIVE_DESTRUCTIVE=1, with the isolated deployment's
# ``bot_manager_sync.bcs_base_url`` pointed at this suite's in-test
# fault proxy. The fault agent exists only at this test boundary —
# production core never grows a test-only hook, and the shared BCS of a
# deployment is never touched without that opt-in. The maintenance legs
# (drain proof, marker CAS clear, backfill) use the deployment's
# maintenance injector exactly as the operations runbook §1/§4/§5/§8
# prescribes, importable only inside that environment.
# ---------------------------------------------------------------------------


@dataclass
class _FaultPlan:
    """Fault behavior for the in-test proxy — test-boundary only."""

    reject_status: int | None = None  # e.g. 403 from the BCS side
    drop_after_request_bytes: bool = False  # accept body, never answer → unknown
    rewrite_total: int | None = None  # answer with a wrong `total` (drift)
    requests: list[dict[str, Any]] = field(default_factory=list)
    lock = threading.Lock()

    def observe(self, method: str, path: str) -> None:
        with self.lock:
            self.requests.append({"method": method, "path": path})


def _start_fault_proxy(config: LiveCaseConfig, plan: _FaultPlan) -> ThreadingHTTPServer:
    """A local proxy forwarding to the real BCS, with injectable faults."""
    upstream = config.destructive_upstream_url

    class Handler(BaseHTTPRequestHandler):
        def _forward(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length) if length else b""
            relay_headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower() not in {"host", "content-length"}
            }
            with plan.lock:
                plan.observe(self.command, self.path)
                drop = plan.drop_after_request_bytes
                observed = len(plan.requests)
            if drop and observed > 1:
                # The request bytes were accepted by the transport: the sender
                # cannot know whether BCS applied them — the unknown posture.
                time.sleep(120)
                return
            with httpx.Client(base_url=upstream, timeout=30.0) as client:
                answered = client.request(
                    self.command, self.path, content=payload, headers=relay_headers
                )
            body = answered.content or b""
            if plan.rewrite_total is not None and body:
                try:
                    envelope = json.loads(body)
                    envelope["data"]["total"] = plan.rewrite_total
                    body = json.dumps(envelope).encode()
                except (ValueError, KeyError, TypeError):
                    pass
            self.send_response(plan.reject_status or answered.status_code)
            for key, value in answered.headers.items():
                if key.lower() not in {"content-length", "transfer-encoding"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_PUT = do_GET = do_POST = do_DELETE = _forward

        def log_message(self, *args: Any) -> None:  # silence proxy chatter
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", config.destructive_proxy_port or 0), Handler
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def destructive_case(live_manager_case):
    """The destructive posture's case — an unambiguous skip without opt-in."""
    case = live_manager_case
    if not case.config.destructive:
        pytest.skip(
            "destructive manager-sync scenario NOT covered by this run: it "
            "requires MANAGER_SYNC_LIVE_DESTRUCTIVE=1 and an isolated "
            "fault-capable deployment posture (module docstring); it must "
            "never run against a shared BCS without that permission"
        )
    return case


@pytest.fixture
def fault_proxy(destructive_case):
    """The in-test fault proxy over the deployment's real BCS.

    The faulted server lives for the test body; the finalizer then leaves
    a *clean* proxy on the case so the case's own teardown sends (and the
    recovery legs) reach the real BCS — a dead proxy would classify
    every later send as unknown and poison the next scenario.
    """
    case = destructive_case
    plan = _FaultPlan()
    server = _start_fault_proxy(case.config, plan)
    try:
        yield plan
    finally:
        server.shutdown()
        server.server_close()
        case._cleanup_proxy = _start_fault_proxy(case.config, _FaultPlan())


def _case_key(case: Any):
    from agentclaw.community.kernel.bot_manager_sync import SyncKey

    return SyncKey(
        avernet_tenant=case.config.backend_tenant,
        env=case.config.backend_env,
        bot_pk=case.config.backend_bot_pk,
    )


def _maintenance_stack(case: Any):
    """Resolve the deployment's maintenance injector parts (runbook §1).

    Only importable inside the deployment's controlled maintenance
    environment; elsewhere the import failure is an honest stop.
    """
    from agentclaw.community.di import DeployProfile, build_injector
    from agentclaw.community.di.modules.bot_manager_sync_module import ManagerSyncStack

    profile = getattr(DeployProfile, case.config.maintenance_profile, None)
    if profile is None:
        pytest.fail(
            f"MANAGER_SYNC_LIVE_MAINTENANCE_PROFILE={case.config.maintenance_profile!r} "
            "names no DeployProfile; the drain/compensation leg needs the "
            "deployment's real profile to build the maintenance injector"
        )
    injector = build_injector(profile=profile)
    stack = injector.get(ManagerSyncStack)
    if not stack.available or stack.coordinator is None:
        pytest.fail(
            f"manager-sync stack unavailable in this posture ({stack.reason}); "
            "the destructive drills need the enabled deployment stack"
        )
    return injector, stack


def _maintenance_repair(case: Any):
    from agentclaw.community.core.bot_collaborator.manager_sync.repair import (
        ManagerSyncRepairService,
    )

    injector, _ = _maintenance_stack(case)
    return injector.get(ManagerSyncRepairService)


def test_backend_success_with_bcs_403_keeps_business_result(destructive_case, fault_proxy):
    case = destructive_case
    plan = fault_proxy
    plan.reject_status = 403
    row = case.add_collaborator("admin", "admin")
    # The business result is the original one: the row committed and the
    # normal HTTP response reports success regardless of BCS's refusal.
    assert row["role"] == "admin"
    case.wait_until(lambda: len(plan.requests) >= 1, timeout_seconds=30)
    # 403 is a permanent refusal for the sender — the roster must not
    # show the new human while the Backend row still exists (the revoked/
    # refused state is observable, not hidden behind a fake synced claim).
    case.wait_until(
        lambda: "admin" not in case.read_manager_ids(), timeout_seconds=30,
    )
    listing = case._backend_client.get(
        "/api/bot/collaborator/list",
        params={
            "bot_id": case.config.backend_bot_id,
            "owner_id": case.config.backend_owner_id,
        },
    )
    assert listing.status_code == 200 and listing.json()["success"] is True
    assert any(
        entry["user_id"] == case._resolve_alias("admin")
        for entry in listing.json()["data"]
    ), "the collaborator row must survive the refused projection"


def test_bcs_total_drift_never_projects_as_confirmed(destructive_case, fault_proxy):
    case = destructive_case
    plan = fault_proxy
    plan.rewrite_total = 0
    case.add_collaborator("member", "member")
    case.wait_until(lambda: len(plan.requests) >= 1, timeout_seconds=30)
    assert plan.requests, "the sender must actually have attempted the PUT"
    # total drift (BCS answered total=0 against a one-id push) is the
    # contract's drift outcome — the roster read remains the evidence.
    case.wait_until(
        lambda: "member" not in case.read_manager_ids(), timeout_seconds=30,
    )


def test_unknown_delivery_blocks_until_confirmed_drain_then_clear(
    destructive_case, fault_proxy,
):
    case = destructive_case
    key = _case_key(case)
    plan = fault_proxy
    case.add_collaborator("member", "member")
    case.wait_until(lambda: len(plan.requests) >= 1, timeout_seconds=30)
    # Hold the answer of the *next* PUT after its bytes were accepted:
    # the sender times out on an in-flight request → unknown outcome.
    with plan.lock:
        plan.drop_after_request_bytes = True
    row = case.add_collaborator("admin", "admin")
    assert row["role"] == "admin", "business result unchanged by the fault"
    case.wait_until(lambda: len(plan.requests) >= 2, timeout_seconds=30)
    # With the sender's attempt unknown, later snapshots for this Bot must be
    # blocked: the admin mutation is admitted but must not land in BCS.
    _, stack = _maintenance_stack(case)
    coordinator = stack.coordinator
    case.wait_until(
        lambda: coordinator.read_blocked(key) is not None, timeout_seconds=30
    )
    assert "admin" not in case.read_manager_ids()
    # §4/§5: prove (deployment evidence recorded in the release validation
    # record), then CAS-clear exactly the blocked attempt with operator +
    # evidence, re-request explicitly, and only then verify convergence. The
    # proxy forwards cleanly for the re-send; the dropped attempt can never
    # be re-sent (its held connection is gone with the drill's stop).
    plan.drop_after_request_bytes = False
    repair = _maintenance_repair(case)
    attempt_id = coordinator.read_blocked(key)
    assert attempt_id, "the unknown attempt's marker must still be present"
    assert repair.clear_confirmed_attempt(
        key,
        attempt_id=attempt_id,
        operator_id="live-acceptance-drill",
        evidence=(
            "drill: isolated destructive posture; the held attempt's bytes "
            "could never be re-sent (connection dropped); BCS server-side "
            "evidence confirmed the attempt never applied; see the release "
            "validation record"
        ),
    ) is True
    verdict = repair.request_bot(key)
    assert verdict in {"queued", "coalesced"}, f"re-request admitted: {verdict}"
    case.wait_until(
        lambda: case.read_manager_ids() >= {"member", "admin"}, timeout_seconds=300,
    )


def test_restart_lost_queue_manual_backfill_recovers_roster(
    destructive_case, fault_proxy,
):
    """A restart-lost in-memory queue is recovered by the §8 manual backfill.

    In this isolated posture the operator restarts the deployed Backend
    (dropping its in-memory queue) right after the collaborator row below
    commits and before its projection ran; the drill then replays the key
    from the maintenance environment and verifies convergence. The proxy
    runs faultless: the recovery leg must see clean sends.
    """
    case = destructive_case
    key = _case_key(case)
    row = case.add_collaborator("backfill_member", "member")
    assert row["role"] == "member"
    repair = _maintenance_repair(case)
    verdict = repair.request_bot(key)
    assert verdict in {"queued", "coalesced"}, (
        f"manual backfill must admit after the restart window: {verdict}"
    )
    case.wait_until(
        lambda: "backfill_member" in case.read_manager_ids(),
        timeout_seconds=300,
    )


def test_owner_check_hold_blocks_and_does_not_auto_transfer(destructive_case, fault_proxy):
    """Backend handoff success must NOT fake a BCS ownership transfer.

    The side-channel only plants the owner-check hold; cross-system
    ownership reconciliation stays a separate, explicit operation (design
    §3.3, runbook §5.3). The drill intentionally ends with the target
    held — hence it MUST STAY LAST IN THIS FILE: any later scenario would
    start on a blocked marker + non-empty roster, and re-running the
    suite needs §5.3 reconciliation first.
    """
    case = destructive_case
    key = _case_key(case)
    case.add_collaborator("admin", "admin")
    case.wait_until(lambda: "admin" in case.read_manager_ids(), timeout_seconds=30)
    original_owner = case.read_bcs_owner_user_id()
    new_owner_id = case._resolve_alias("handoff_target")
    handoff = case._backend_client.post(
        "/api/bots/owner-handoffs",
        json={
            "bot_pk": case.config.backend_bot_pk,
            "new_owner_id": new_owner_id,
            "request_key": f"live-{uuid.uuid4().hex[:16]}",
        },
    )
    assert handoff.status_code == 200, handoff.text
    operation_id = handoff.json()["data"]["operation_id"]

    def _handoff_state() -> str:
        response = case._backend_client.get(f"/api/bots/owner-handoffs/{operation_id}")
        return response.json()["data"]["status"]

    case.wait_until(lambda: _handoff_state() == "SUCCEEDED", timeout_seconds=300)
    # No auto-transfer: BCS ownership is untouched by the Backend handoff.
    assert case.read_bcs_owner_user_id() == original_owner
    # And the hold is present, blocking further roster sends for this target.
    _, stack = _maintenance_stack(case)
    case.wait_until(
        lambda: stack.coordinator.read_blocked(key) is not None, timeout_seconds=60
    )
    case.update_collaborator("admin", role="member")
    time.sleep(5)
    assert case.read_manager_ids() == {"admin"}, (
        "roster must not drift past the owner-check hold"
    )
    # Deliberate end state; see above for why this test stays last: §5.3
    # reconciliation is ops' both-sides call, never faked or cleared here.
    case._teardown_note = (
        "owner-check drill: case target intentionally left blocked pending the "
        "runbook §5.3 both-sides owner reconciliation by operations"
    )
