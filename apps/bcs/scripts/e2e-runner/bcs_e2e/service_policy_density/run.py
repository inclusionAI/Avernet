"""Offline execution and receipt projection for service-policy density.

The projector replays the forty-point source-anchored roster against an
injected transport and never opens a socket, resolves a credential, or
contacts preprod.  Two execution postures are admitted:

* owned-local Singlebox is deterministic, so the local tier computes offline
  through the shipped ReplayTransport fixture (the frozen Singlebox story
  envelope: two legacy-encoded disposable Groups persisting nothing, the
  retired settings surface answering 410 service_policy_retired, two distinct
  running service_invocation Sessions with persisted inputs, the 409
  running-shaped reactivation refusal with input run 0 preserved, the
  completed chat Session lifecycle, the 400 not-service_invocation conversion
  refusal, and the six-disposition owned-local teardown);
* preprod has no offline演算 posture: without an injected admitted live
  transport the receipt fail-closes to ``BLOCKED_INVENTORIED`` with the named
  gap ``SERVICE_POLICY_DENSITY_PREPROD_TRANSPORT_GAP`` and every obligation
  stays ``NOT_RUN`` - a planned remote leg never becomes PASS offline.  The
  registered cancellation fence is disclosed: the shared preprod driver Bot is
  never deleted (SKIPPED_SHARED_DRIVER_BOT).

PASS criteria follow the structured-density slice: every projected
obligation evaluates PASS, any failure or transport exception fails that row
and marks every later row NOT_RUN with ``BLOCKED_AFTER_ASSERTION_FAILURE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..profile import fingerprint
from ..run_state import RunStateError
from .contract import (
    CASE_ID,
    DENSITY_ASSERTION_COUNT,
    DENSITY_FIELD_COUNT,
    DENSITY_PARENT_COUNT,
    PROJECTION_CONTRACT,
    service_policy_density_plan,
)
from .obligations import FIELD_SITE_COUNT
from .profile import validate_service_policy_density_profile

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"

GAP_ID = "SERVICE_POLICY_DENSITY_PREPROD_TRANSPORT_GAP"
GAP_KIND = "PREPROD_TRANSPORT_NOT_ADMITTED_OFFLINE"
GAP_DETAIL = (
    "preprod retired-service-policy density requires an admitted bcs-direct "
    "legacy-face live transport; the offline projector fail-closes and keeps "
    "every obligation NOT_RUN instead of charging unexecuted rows")
GAP_RESOLUTION = (
    "inject the admitted live transport through the run seam in the mainline "
    "serial flow; preprod admission and remote release validation remain "
    "separate gates")

# Deterministic owned-local replay fixtures.  Group/Session identifiers and
# input payloads mirror the frozen upstream story literal for the legacy
# journey; the teardown dispositions are the owned-local six-disposition
# landmark (three disposable Sessions, two disposable Groups, the registered
# disposable driver Bot) versus the preprod fence that skips the shared
# driver Bot deletion.
GROUP_OBJECT_ID = "grp-policy-density-object"
GROUP_NULL_ID = "grp-policy-density-null"
INVOCATION_FIRST_SESSION_ID = "svc-policy-density-1"
INVOCATION_SECOND_SESSION_ID = "svc-policy-density-2"
CHAT_SESSION_ID = "chat-policy-density-1"
TEARDOWN_FENCE = "SKIPPED_SHARED_DRIVER_BOT"

LOCAL_TEARDOWN_DISPOSITIONS = (
    "session_delete", "session_delete", "session_delete",
    "group_delete", "group_delete", "bot_delete",
)
PREPROD_TEARDOWN_DISPOSITIONS = (
    "session_delete", "session_delete", "session_delete",
    "group_delete", "group_delete", TEARDOWN_FENCE,
)

INVOCATION_CREATE_OPERATIONS = (
    "service_policy.invocation_create_first",
    "service_policy.invocation_create_second",
)


@dataclass(frozen=True)
class Observation:
    """One deterministic response observation for a request site."""

    status: int
    body: Any


class ReplayTransport:
    """Offline replay transport with call recording and no network capability."""

    def __init__(self, observations: dict[str, Observation]):
        self.observations = observations
        self.calls: list[tuple[str, str]] = []

    def request(self, operation: str, obligation_id: str) -> Observation:
        self.calls.append((operation, obligation_id))
        if operation not in self.observations:
            raise RunStateError(f"service policy density replay lacks operation {operation}")
        return self.observations[operation]


def owned_local_replay_observations(profile: dict[str, Any]) -> dict[str, Observation]:
    """Frozen Singlebox story envelope for the eighteen roster request sites.

    The legacy encodings persist nothing, so neither the created nor the
    read-back Group envelope carries ``service_spec`` or
    ``retired_service_spec``; the absence obligations charge exactly that.
    """
    object_group = {"id": GROUP_OBJECT_ID, "label": "Retired policy compatibility"}
    null_group = {"id": GROUP_NULL_ID, "label": "Retired policy compatibility"}
    return {
        "service_policy.ensure_human": Observation(200, {}),
        "service_policy.register_bot": Observation(
            200, {"bot_uuid": "bot-policy-density-driver"}),
        "service_policy.group_create_object": Observation(200, dict(object_group)),
        "service_policy.group_read_object": Observation(200, dict(object_group)),
        "service_policy.group_create_null": Observation(201, dict(null_group)),
        "service_policy.group_read_null": Observation(200, dict(null_group)),
        "service_policy.settings_patch_retired": Observation(
            410, {"error": "service_policy_retired"}),
        "service_policy.invocation_create_first": Observation(
            202, {"session_id": INVOCATION_FIRST_SESSION_ID,
                  "session_kind": "service_invocation", "status": "running"}),
        "service_policy.invocation_read_first": Observation(200, {"input": {"run": 0}}),
        "service_policy.invocation_create_second": Observation(
            202, {"session_id": INVOCATION_SECOND_SESSION_ID,
                  "session_kind": "service_invocation", "status": "running"}),
        "service_policy.invocation_read_second": Observation(200, {"input": {"run": 1}}),
        "service_policy.reactivation_denied": Observation(
            409, {"error": "session_running_conflict"}),
        "service_policy.reactivation_read": Observation(200, {"input": {"run": 0}}),
        "service_policy.chat_create": Observation(
            201, {"session_id": CHAT_SESSION_ID}),
        "service_policy.chat_complete": Observation(200, {"status": "completed"}),
        "service_policy.conversion_denied": Observation(
            400, {"error": "conversion refused: completed chat Session kind "
                           "not service_invocation"}),
        "service_policy.chat_final_read": Observation(
            200, {"status": "completed", "output": {"ok": True}}),
        "service_policy.teardown_dispositions": Observation(
            200, {"dispositions": list(LOCAL_TEARDOWN_DISPOSITIONS)}),
    }


def owned_local_replay_transport(profile: dict[str, Any]) -> ReplayTransport:
    """Construct the deterministic owned-local replay transport offline."""
    return ReplayTransport(owned_local_replay_observations(profile))


def _dig(value: Any, path: str) -> Any:
    for part in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _initial_context(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "tier": profile["environment"]["tier"],
        "invocation_session_ids": [],
    }


def _resolve(expected: Any, context: dict[str, Any]) -> Any:
    return {
        "RUN_ZERO": {"run": 0},
        "RUN_ONE": {"run": 1},
        "CHAT_OUTPUT": {"ok": True},
    }.get(expected, expected)


def _evaluate(row: dict[str, Any], observation: Observation,
              context: dict[str, Any]) -> tuple[bool, Any]:
    comparator = row["comparator"]
    if comparator == "session_ids_distinct":
        ids = context["invocation_session_ids"]
        return len(ids) >= 2 and len(set(ids)) == 2, list(ids)
    if comparator == "teardown_dispositions":
        expected = (PREPROD_TEARDOWN_DISPOSITIONS if context["tier"] == "preprod"
                    else LOCAL_TEARDOWN_DISPOSITIONS)
        body = observation.body
        observed = body.get("dispositions") if isinstance(body, dict) else None
        return observed == list(expected), observed
    if row["kind"] == "status" or comparator in ("http_status", "http_status_enum"):
        # Status comparators own the wire status regardless of row level; the
        # legacy double-encoding field rows (F01/F07) charge the enum here.
        if comparator == "http_status_enum":
            return observation.status in row["expected"], observation.status
        return observation.status == row["expected"], observation.status
    observed = _dig(observation.body, row["field"] or "")
    if comparator == "field_absent":
        body = observation.body
        return isinstance(body, dict) and row["field"] not in body, None
    if comparator == "field_contains":
        return isinstance(observed, str) and row["expected"] in observed, observed
    return observed == _resolve(row["expected"], context), observed


def _obligation_receipt(row: dict[str, Any], status: str, *, observed: Any = None,
                        error_type: str | None = None) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "obligation_id": row["obligation_id"],
        "parent_obligation": row["parent_obligation"],
        "level": row["level"],
        "family": row["family"],
        "status": status,
        "density": row["density"],
        "comparator": row["comparator"],
        "source": {
            "file": row["source_file"],
            "line": row["source_line"],
            "quote": row["source_quote"],
            "request_line": row["request_line"],
            "request_quote": row["request_quote"],
            "operation": row["operation"],
            "field": row["field"],
            "expected": row["expected"],
            "located": row["located"],
        },
    }
    if observed is not None:
        receipt["observed"] = observed
    if error_type is not None:
        receipt["error_type"] = error_type
    return receipt


def _gap() -> dict[str, str]:
    return {"id": GAP_ID, "kind": GAP_KIND, "detail": GAP_DETAIL, "resolution": GAP_RESOLUTION}


def run_service_policy_density(*, profile: dict[str, Any],
                               transport: ReplayTransport | None = None) -> dict[str, Any]:
    """Project the forty-point roster offline against an admitted profile.

    ``transport`` defaults to the deterministic owned-local replay fixture on
    the local tier (offline演算 of the frozen Singlebox story envelope).  The
    preprod tier fail-closes offline to ``BLOCKED_INVENTORIED`` with the named
    gap and a full NOT_RUN receipt; supplying an injected transport (the
    future live-admission seam) evaluates it with identical PASS criteria,
    including the preprod teardown chip fence.
    """
    profile = validate_service_policy_density_profile(profile)
    plan = service_policy_density_plan()
    rows = plan["obligations"]
    context = _initial_context(profile)
    tier = profile["environment"]["tier"]
    environment = {
        "tier": tier,
        "id": profile["environment"]["id"],
        "entrypoint_kind": profile["entrypoint"]["kind"],
        "api_face": profile["entrypoint"]["api_face"],
    }
    executor = ("owned-local-singlebox-replay" if tier == "local"
                else "preprod-bcs-direct-legacy")
    scope = {
        "parent_obligation_count": DENSITY_PARENT_COUNT,
        "field_site_count": FIELD_SITE_COUNT,
        "expected_obligation_count": DENSITY_ASSERTION_COUNT,
        "new_field_obligation_count": DENSITY_FIELD_COUNT,
        "delta": DENSITY_FIELD_COUNT,
    }

    if transport is None and tier == "preprod":
        assertions = [_obligation_receipt(row, STATUS_NOT_RUN,
                                          error_type="PREPROD_EXECUTION_NOT_RUN")
                      for row in rows]
        return {
            "schema_version": 1,
            "contract": PROJECTION_CONTRACT,
            "case_id": CASE_ID,
            "parent": plan["parent"],
            "status": STATUS_BLOCKED_INVENTORIED,
            "environment": environment,
            "executor": executor,
            "profile_digest": fingerprint(profile),
            "case_scope": dict(scope),
            "gap": _gap(),
            "assertions": assertions,
            "execution": {
                "status": STATUS_NOT_RUN,
                "completed_count": 0,
                "total_count": DENSITY_ASSERTION_COUNT,
                "error_type": "PREPROD_EXECUTION_NOT_RUN",
            },
            "reports": {
                "expected_obligation_count": DENSITY_ASSERTION_COUNT,
                "observed_obligation_count": len(assertions),
                "executed_obligation_count": 0,
                "pass_count": 0,
            },
            "reconciliation": plan["reconciliation"],
            "remote_validation": STATUS_NOT_RUN,
            "grants_release_credit": False,
        }

    if transport is None:
        transport = owned_local_replay_transport(profile)

    assertions: list[dict[str, Any]] = []
    failed = False
    for row in rows:
        if failed:
            assertions.append(_obligation_receipt(
                row, STATUS_NOT_RUN, error_type="BLOCKED_AFTER_ASSERTION_FAILURE"))
            continue
        try:
            observation = transport.request(row["operation"], row["obligation_id"])
            if row["operation"] in INVOCATION_CREATE_OPERATIONS:
                session_id = _dig(observation.body, "session_id")
                if session_id is not None:
                    context["invocation_session_ids"].append(str(session_id))
            passed, observed = _evaluate(row, observation, context)
            assertions.append(_obligation_receipt(
                row, STATUS_PASS if passed else STATUS_FAIL, observed=observed))
            if not passed:
                failed = True
        except Exception as error:  # transport failures are evidence, not PASS
            assertions.append(_obligation_receipt(
                row, STATUS_FAIL, error_type=type(error).__name__))
            failed = True
    status = STATUS_PASS if not failed else STATUS_FAIL
    return {
        "schema_version": 1,
        "contract": PROJECTION_CONTRACT,
        "case_id": CASE_ID,
        "parent": plan["parent"],
        "status": status,
        "environment": environment,
        "executor": executor,
        "profile_digest": fingerprint(profile),
        "case_scope": dict(scope),
        "assertions": assertions,
        "execution": {
            "status": status,
            "completed_count": sum(item["status"] == STATUS_PASS for item in assertions),
            "total_count": DENSITY_ASSERTION_COUNT,
            "error_type": None if status == STATUS_PASS else "SERVICE_POLICY_DENSITY_ASSERTION_FAILED",
        },
        "reports": {
            "expected_obligation_count": DENSITY_ASSERTION_COUNT,
            "observed_obligation_count": len(assertions),
            "executed_obligation_count": sum(item["status"] != STATUS_NOT_RUN for item in assertions),
            "pass_count": sum(item["status"] == STATUS_PASS for item in assertions),
        },
        "reconciliation": plan["reconciliation"],
        "remote_validation": STATUS_NOT_RUN,
        "grants_release_credit": False,
    }
