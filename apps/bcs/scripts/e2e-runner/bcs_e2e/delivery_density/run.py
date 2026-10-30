"""Offline execution and receipt projection for delivery-policy density.

The projector replays the 36-point source-anchored roster against an injected
transport and never opens a socket, resolves a credential, or contacts
preprod.  Two execution postures are admitted:

* owned-local Singlebox is deterministic, so the local tier computes offline
  through the shipped ReplayTransport fixture (the frozen Singlebox story
  envelope: base version 7, the max_context_messages toggle 24 -> 23, the
  explicit 409 conflict envelope, full restoration to version 9);
* preprod has no offline演算 posture: without an injected admitted live
  transport the receipt fail-closes to ``BLOCKED_INVENTORIED`` with the named
  gap ``DELIVERY_DENSITY_PREPROD_TRANSPORT_GAP`` and every obligation stays
  ``NOT_RUN`` - a planned remote leg never becomes PASS offline.

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
    delivery_density_plan,
)
from .obligations import FIELD_SITE_COUNT
from .profile import validate_delivery_density_profile

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"

GAP_ID = "DELIVERY_DENSITY_PREPROD_TRANSPORT_GAP"
GAP_KIND = "PREPROD_TRANSPORT_NOT_ADMITTED_OFFLINE"
GAP_DETAIL = (
    "preprod delivery-policy density requires an admitted bcs-direct "
    "legacy-face live transport; the offline projector fail-closes and keeps "
    "every obligation NOT_RUN instead of charging unexecuted rows")
GAP_RESOLUTION = (
    "inject the admitted live transport through the run seam in the mainline "
    "serial flow; preprod admission and remote release validation remain "
    "separate gates")

# Deterministic owned-local replay seed.  Comparators bind relatively (base
# version plus one/two), so the seed is fixture data only, never a contract.
BASE_VERSION = 7
POLICY_ORIGINAL = {"max_context_messages": 24}
POLICY_UPDATED = {"max_context_messages": 23}
CONFLICT_ERROR_CODE = "delivery_policy_version_conflict"
FIXTURE_UPDATED_AT_MS = 1698765432000


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
            raise RunStateError(f"delivery density replay lacks operation {operation}")
        return self.observations[operation]


def owned_local_replay_observations(profile: dict[str, Any]) -> dict[str, Observation]:
    """Frozen Singlebox story envelope for the ten reviewed request sites.

    ``updated_by`` stamps the admitted profile's Human actor exactly as the
    upstream story comparator ``human_${BCS_MOCK_USER_ID}`` does; no credential
    value is involved.
    """
    human_actor = "human_" + profile["human"]["user_id"]
    return {
        "delivery_policy.human_read": Observation(
            200, {"version": BASE_VERSION, "policy": dict(POLICY_ORIGINAL),
                  "updated_by": human_actor, "updated_at_ms": FIXTURE_UPDATED_AT_MS}),
        "delivery_policy.bot_read_denied": Observation(403, None),
        "delivery_policy.bot_write_denied": Observation(403, None),
        "delivery_policy.denied_preserves": Observation(
            200, {"version": BASE_VERSION, "policy": dict(POLICY_ORIGINAL),
                  "updated_by": human_actor, "updated_at_ms": FIXTURE_UPDATED_AT_MS}),
        "delivery_policy.cas_update": Observation(
            200, {"version": BASE_VERSION + 1, "policy": dict(POLICY_UPDATED),
                  "updated_by": human_actor, "updated_at_ms": FIXTURE_UPDATED_AT_MS}),
        "delivery_policy.readback": Observation(
            200, {"version": BASE_VERSION + 1, "policy": dict(POLICY_UPDATED),
                  "updated_by": human_actor, "updated_at_ms": FIXTURE_UPDATED_AT_MS}),
        "delivery_policy.stale_rejected": Observation(
            409, {"error": CONFLICT_ERROR_CODE}),
        "delivery_policy.conflict_preserves": Observation(
            200, {"version": BASE_VERSION + 1, "policy": dict(POLICY_UPDATED),
                  "updated_by": human_actor, "updated_at_ms": FIXTURE_UPDATED_AT_MS}),
        "delivery_policy.restore": Observation(
            200, {"version": BASE_VERSION + 2, "policy": dict(POLICY_ORIGINAL),
                  "updated_by": human_actor, "updated_at_ms": FIXTURE_UPDATED_AT_MS}),
        "delivery_policy.restored": Observation(
            200, {"version": BASE_VERSION + 2, "policy": dict(POLICY_ORIGINAL),
                  "updated_by": human_actor, "updated_at_ms": FIXTURE_UPDATED_AT_MS}),
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
        "base_version": BASE_VERSION,
        "human_actor": "human_" + profile["human"]["user_id"],
        "policy_original": POLICY_ORIGINAL,
        "policy_updated": POLICY_UPDATED,
        "conflict_error": CONFLICT_ERROR_CODE,
    }


def _resolve(expected: Any, context: dict[str, Any]) -> Any:
    return {
        "BASE_VERSION": context["base_version"],
        "BASE_VERSION_PLUS_ONE": context["base_version"] + 1,
        "BASE_VERSION_PLUS_TWO": context["base_version"] + 2,
        "POLICY_ORIGINAL": context["policy_original"],
        "POLICY_UPDATED": context["policy_updated"],
        "HUMAN_ACTOR": context["human_actor"],
    }.get(expected, expected)


def _envelope_check(field: str, shape: str, observed: Any) -> bool:
    if observed is None:
        return False
    if shape == "NON_NEGATIVE_INTEGER":
        return type(observed) is int and observed >= 0
    if shape == "OBJECT":
        return isinstance(observed, dict) and bool(observed)
    if shape == "NON_EMPTY_STRING":
        return isinstance(observed, str) and bool(observed.strip())
    return False


def _evaluate(row: dict[str, Any], observation: Observation,
              context: dict[str, Any]) -> tuple[bool, Any]:
    if row["kind"] == "status":
        return observation.status == row["expected"], observation.status
    observed = _dig(observation.body, row["field"] or "")
    if row["aspect"] == "envelope":
        return _envelope_check(row["field"] or "", row["shape"] or "", observed), observed
    if row["expected"] == "NON_NEGATIVE_INTEGER":
        text = str(observed) if observed is not None else ""
        return text.isdigit() and int(text) >= 0, observed
    return observed == _resolve(row["expected"], context), observed


def _obligation_receipt(row: dict[str, Any], status: str, *, observed: Any = None,
                        error_type: str | None = None) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "obligation_id": row["obligation_id"],
        "parent_obligation": row["parent_obligation"],
        "level": row["level"],
        "aspect": row["aspect"],
        "status": status,
        "density": row["density"],
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


def run_delivery_density(*, profile: dict[str, Any],
                         transport: ReplayTransport | None = None) -> dict[str, Any]:
    """Project the 36-point roster offline against an admitted profile.

    ``transport`` defaults to the deterministic owned-local replay fixture on
    the local tier (offline演算 of the frozen Singlebox story envelope).  The
    preprod tier fail-closes offline to ``BLOCKED_INVENTORIED`` with the named
    gap and a full NOT_RUN receipt; supplying an injected transport (the
    future live-admission seam) evaluates it with identical PASS criteria.
    """
    profile = validate_delivery_density_profile(profile)
    plan = delivery_density_plan()
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
            "error_type": None if status == STATUS_PASS else "DELIVERY_DENSITY_ASSERTION_FAILED",
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
