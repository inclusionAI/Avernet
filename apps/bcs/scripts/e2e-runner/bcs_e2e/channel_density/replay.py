"""Credential-free offline replay for the Wave-C channel dual-story density contract.

The replay consumes only an in-memory observation dictionary.  It never starts
bcs-cli, opens a network connection, resolves a credential, or mutates a business
resource.  The local tier mirrors the two frozen legacy owned-local sim arms
(LOCAL-CHANNEL-HTTP-01 and LOCAL-CHANNEL-CLI-01) with deterministic dispositions;
the preprod tier defaults to an honest NOT_RUN projection with the four named
registrations, and no tier ever fabricates a PASS.  Malformed or negative
observations fail closed, missing observations stay NOT_RUN, and a failure
cascades to later roster rows exactly like the owned-local executors do.
"""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError
from .contract import (
    CHANNEL_BINDINGS_PATH_FACE_DRIFT_ID,
    CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID,
    CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID,
    CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID,
    CLI_PARSED_OBLIGATION_COUNT,
    CLI_PARENT_OBLIGATION_COUNT,
    DENSITY_CONTRACT,
    HTTP_PARSED_OBLIGATION_COUNT,
    HTTP_PARENT_OBLIGATION_COUNT,
    LEGACY_CLI_REQUIREMENTS,
    LEGACY_DUAL_COMMANDS,
    LEGACY_HTTP_CASE_IDS,
    LEGACY_HTTP_REQUIREMENTS,
    LEGACY_CLI_CASE_IDS,
    NAMED_REGISTRATIONS,
    PARSED_OBLIGATION_COUNT,
    PARENT_OBLIGATION_COUNT,
    DENSITY_OBLIGATION_COUNT,
    RESIDUAL_COUNT_GAP,
    SOURCE_REVISION,
    STORY_CLI,
    STORY_HTTP,
    UPSTREAM_COLOR_BASIS,
    channel_density_plan,
)

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"

BLOCKED_AFTER_ASSERTION_FAILURE = "BLOCKED_AFTER_ASSERTION_FAILURE"
OBSERVATION_MISMATCH = "OBSERVATION_MISMATCH"
PREPROD_EXECUTION_NOT_RUN = "PREPROD_EXECUTION_NOT_RUN"

LOCAL_EXECUTION = "owned-local-offline-replay"
PREPROD_EXECUTION = "preprod-not-run"

DESIGN_RED_CONVERSATION_IDS = ("CHANNEL-CLI.conversation-empty", "CHANNEL-CLI-DENSITY.D05")
NOOP_404_HTTP_IDS = (
    "CHANNEL.pause-missing", "CHANNEL.delete-missing",
    "CHANNEL-HTTP-DENSITY.D05", "CHANNEL-HTTP-DENSITY.D06",
)
DISABLED_BRIDGE_CLI_IDS = ("CHANNEL-CLI.bind-redacted", "CHANNEL-CLI-DENSITY.D01")
FIXTURE_HELPER_IDS = ("CHANNEL-CLI-DENSITY.D02",)
ALL_REGISTRATION_IDS = (
    CHANNEL_HTTP_NOOP_404_POSTURE_DIVERGENCE_ID,
    CHANNEL_SIM_DINGTALK_CREDENTIAL_ABSENT_DESIGN_RED_ID,
    CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID,
    CHANNEL_BINDINGS_PATH_FACE_DRIFT_ID,
)

def named_registration_ids() -> tuple:
    """Return the four named registrations this density projection carries."""
    return ALL_REGISTRATION_IDS


PREPROD_EXECUTION_GAP = {
    "id": "CHANNEL-DENSITY-PREPROD-EXECUTION-GAP",
    "status": STATUS_NOT_RUN,
    "detail": (
        "preprod density replay is not executed by this offline package: the legacy dual-channel "
        "envelopes keep their retained r8 conclusions (dual-channel-bindings-run PASS 4/4 public "
        "obligations; dual-channel-run BLOCKED 0/9 with ChannelCliRunnerError at bind_disabled_bridge), "
        "but this density projection opens no transport and injects no live evidence"
    ),
    "resolution": (
        "mainline may later inject an authorized live transport observation set; this package never "
        "opens one and never converts the retained envelope verdicts into density PASSes"
    ),
}

_LOCAL_CLASSIFICATION_DEFAULT = (
    "owned-local credential-free replay mirroring the frozen legacy dual channel envelope; "
    "no remote execution, no release credit"
)
_LOCAL_CLASSIFICATION_NOOP = (
    "owned-local sim no-op admission (basis DISABLED_BRIDGE_AND_EMPTY_OWNED_DATABASE); upstream poolab "
    "red on the public-v1 404 posture edge; PUBLIC-CHANNEL-BINDINGS-01 keeps the requirement blocked; "
    "never normalized to a remote PASS"
)
_LOCAL_CLASSIFICATION_DESIGN_RED = (
    "owned-local empty owned-table projection (sim replies items == []); upstream red-by-design from "
    "the sim DingTalk credential absence; real DingTalk/OAuth conversation lookup stays NOT_RUN as "
    "material gap M7"
)
_LOCAL_CLASSIFICATION_GUARD = (
    "conditional branch dormant under the sim disabled-bridge bind rejection; premise discharged by "
    "the parent CHANNEL-CLI.bind-redacted envelope"
)
_LOCAL_CLASSIFICATION_HELPER = (
    "fixture helper assertion anchored at the declared helper span (58, 69) of cli-stories.sh"
)
_LOCAL_CLASSIFICATION_PREPROD = (
    "preprod density replay is not executed by this offline package; the row stays NOT_RUN with its "
    "named registrations and no fabricated PASS"
)


def mock_observations() -> dict[str, Any]:
    """Return the deterministic credential-free local replay fixture.

    The fixture mirrors the two retained legacy owned-local sim outcomes: the
    HTTP arm observes the disabled-bridge 400 rejection, empty owned lists, and
    the two no-op admissions; the CLI arm observes the redacted bind failure,
    the Group/Session lifecycle, the empty DingTalk conversation projection,
    the unbind no-op acknowledgement, and the exact session/group deletes.
    """
    return {
        "parents": {obligation_id: STATUS_PASS for obligation_id in
                    list(LEGACY_HTTP_REQUIREMENTS) + list(LEGACY_CLI_REQUIREMENTS)},
        "fields": {
            "CHANNEL-HTTP-DENSITY.D01": {"status": STATUS_PASS, "value": "bad_request"},
            "CHANNEL-HTTP-DENSITY.D02": {
                "status": STATUS_PASS,
                "value": "code bad_request; channel bridge is disabled",
            },
            "CHANNEL-HTTP-DENSITY.D03": {"status": STATUS_PASS, "value": "1"},
            "CHANNEL-HTTP-DENSITY.D04": {"status": STATUS_PASS, "value": []},
            "CHANNEL-HTTP-DENSITY.D05": {"status": STATUS_PASS, "value": True},
            "CHANNEL-HTTP-DENSITY.D06": {"status": STATUS_PASS, "value": True},
            "CHANNEL-HTTP-DENSITY.D07": {"status": STATUS_PASS, "value": []},
            "CHANNEL-CLI-DENSITY.D01": {
                "status": STATUS_PASS,
                "value": "dormant_premise_discharged_by_parent",
            },
            "CHANNEL-CLI-DENSITY.D02": {"status": STATUS_PASS, "value": "group-cli-channel-density-1"},
            "CHANNEL-CLI-DENSITY.D03": {"status": STATUS_PASS, "value": "1"},
            "CHANNEL-CLI-DENSITY.D04": {"status": STATUS_PASS, "value": "session-cli-channel-density-1"},
            "CHANNEL-CLI-DENSITY.D05": {"status": STATUS_PASS, "value": []},
            "CHANNEL-CLI-DENSITY.D06": {"status": STATUS_PASS, "value": True},
            "CHANNEL-CLI-DENSITY.D07": {"status": STATUS_PASS, "value": []},
        },
        "basis": (
            "deterministic owned-local sim mirror of LOCAL-CHANNEL-HTTP-01 (legacy suite "
            "owned-local-channel-http-v1) and LOCAL-CHANNEL-CLI-01 (legacy suite "
            "owned-local-channel-cli-v1); credential-free; no CLI, network, or business resource"
        ),
    }


def _compare(comparator: str, observed: Any, expected: Any) -> bool:
    if comparator in ("equals", "envelope", "guard"):
        return type(observed) is type(expected) and observed == expected
    if comparator == "contains":
        return (isinstance(observed, str) and isinstance(expected, str)
                and expected.lower() in observed.lower())
    if comparator == "non_empty":
        return isinstance(observed, str) and bool(observed)
    return False


def _parent_status(entry: Any) -> tuple[str, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None
    if entry == STATUS_PASS:
        return STATUS_PASS, None
    if entry == STATUS_NOT_RUN:
        return STATUS_NOT_RUN, None
    return STATUS_FAIL, OBSERVATION_MISMATCH


def _atom_status(entry: Any) -> tuple[str, str | None]:
    if entry is None:
        return STATUS_NOT_RUN, None
    if not isinstance(entry, dict):
        return STATUS_FAIL, OBSERVATION_MISMATCH
    status = entry.get("status")
    if status == STATUS_NOT_RUN:
        return STATUS_NOT_RUN, None
    if status != STATUS_PASS:
        return STATUS_FAIL, OBSERVATION_MISMATCH
    if "value" not in entry:
        return STATUS_FAIL, OBSERVATION_MISMATCH
    return STATUS_PASS, None


def _evidence_event(obligation_id: str, level: str) -> str:
    if obligation_id in DESIGN_RED_CONVERSATION_IDS:
        return "owned_local_empty_table_projection"
    if obligation_id in NOOP_404_HTTP_IDS:
        return "disabled_bridge_noop_admission"
    if level == "PARENT":
        return "legacy_dual_envelope_mirror"
    if obligation_id == "CHANNEL-CLI-DENSITY.D01":
        return "conditional_guard_discharge"
    if obligation_id == "CHANNEL-CLI-DENSITY.D02":
        return "fixture_helper_assertion"
    return "sim_assertion_mirror"


def _local_classification(obligation_id: str) -> str:
    if obligation_id in NOOP_404_HTTP_IDS:
        return _LOCAL_CLASSIFICATION_NOOP
    if obligation_id in DESIGN_RED_CONVERSATION_IDS:
        return _LOCAL_CLASSIFICATION_DESIGN_RED
    if obligation_id == "CHANNEL-CLI-DENSITY.D01":
        return _LOCAL_CLASSIFICATION_GUARD
    if obligation_id in DISABLED_BRIDGE_CLI_IDS:
        return (_LOCAL_CLASSIFICATION_DEFAULT + "; sim pure-JSON redaction posture registered as "
                + CHANNEL_CLI_DISABLED_BRIDGE_DIVERGENCE_ID)
    if obligation_id in FIXTURE_HELPER_IDS:
        return _LOCAL_CLASSIFICATION_HELPER
    return _LOCAL_CLASSIFICATION_DEFAULT


def _row_receipt(obligation: dict[str, Any], status: str, error_type: str | None,
                 evidence_event: str, classification: str) -> dict[str, Any]:
    return {
        "obligation_id": obligation["obligation_id"],
        "story": obligation["story"],
        "level": obligation["level"],
        "method": obligation["method"],
        "status": status,
        "error_type": error_type,
        "source": {
            "file": obligation["source_file"],
            "line_start": obligation["line_start"],
            "line_end": obligation["line_end"],
            "quote": obligation["quote"],
            "located": True,
        },
        "evidence_event": evidence_event,
        "upstream_color": obligation["upstream_color"],
        "upstream_color_basis": obligation["upstream_color_basis"],
        "leaf_classification": obligation["leaf_classification"],
        "named_registrations": list(obligation["named_registrations"]),
        "local_classification": classification,
    }


def _plan_rows() -> list[dict[str, Any]]:
    """Return the 29 combined roster rows in frozen parent-then-atom order."""
    plan = channel_density_plan()
    rows = []
    for story_id in (STORY_HTTP, STORY_CLI):
        rows.extend(plan["stories"][story_id]["obligations"])
    return rows


def _registration_registry() -> dict[str, Any]:
    registry = {}
    for registration_id, registration in NAMED_REGISTRATIONS.items():
        applies = [row["obligation_id"] for row in _plan_rows()
                   if registration_id in row["named_registrations"]]
        entry = dict(registration)
        entry["applies_to_obligation_ids"] = applies
        registry[registration_id] = entry
    return registry


def project(observations: dict[str, Any] | None = None, *, tier: str = "local") -> dict[str, Any]:
    """Project observations into a 29-row honest dual-story receipt.

    The local tier defaults to the deterministic credential-free fixture; the
    preprod tier defaults to an empty set, leaving every row NOT_RUN with
    BLOCKED_INVENTORIED overall and the four named registrations attached.  A
    preprod caller may inject an authorized live observation set, but this
    package never opens a transport, remote_validation stays NOT_RUN, and no
    release credit is granted in any tier.
    """
    if tier not in ("local", "preprod"):
        raise RunStateError("channel density replay tier must be local or preprod")
    injected_preprod = tier == "preprod" and observations is not None
    if observations is None:
        observations = {} if tier == "preprod" else mock_observations()
    if not isinstance(observations, dict):
        raise RunStateError("channel density replay observations must be an object")
    plan = channel_density_plan()
    plan_rows = _plan_rows()
    if len(plan_rows) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("channel density replay plan caliber changed")
    rows: list[dict[str, Any]] = []
    blocked = False
    for obligation in plan_rows:
        obligation_id = obligation["obligation_id"]
        if blocked:
            rows.append(_row_receipt(
                obligation, STATUS_NOT_RUN, BLOCKED_AFTER_ASSERTION_FAILURE,
                "blocked_after_assertion_failure",
                "blocked after an earlier assertion failure in roster order; no fabricated PASS"))
            continue
        if tier == "preprod" and not injected_preprod:
            rows.append(_row_receipt(
                obligation, STATUS_NOT_RUN, None, PREPROD_EXECUTION_NOT_RUN.lower(),
                _LOCAL_CLASSIFICATION_PREPROD))
            continue
        if obligation["level"] == "PARENT":
            entry = observations.get("parents", {}).get(obligation_id)
            status, error_type = _parent_status(entry)
        else:
            entry = observations.get("fields", {}).get(obligation_id)
            status, error_type = _atom_status(entry)
            if status == STATUS_PASS and obligation["comparator"] != "envelope":
                if not _compare(obligation["comparator"], entry.get("value"), obligation["expected"]):
                    status, error_type = STATUS_FAIL, OBSERVATION_MISMATCH
        classification = (_LOCAL_CLASSIFICATION_PREPROD if status == STATUS_NOT_RUN and tier == "preprod"
                          else _local_classification(obligation_id))
        rows.append(_row_receipt(
            obligation, status, error_type,
            _evidence_event(obligation_id, obligation["level"]), classification))
        if status == STATUS_FAIL:
            blocked = True
    counts = {status: sum(1 for row in rows if row["status"] == status)
              for status in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN)}
    if counts[STATUS_PASS] == PARSED_OBLIGATION_COUNT:
        overall = STATUS_PASS
    elif counts[STATUS_FAIL]:
        overall = STATUS_FAIL
    else:
        overall = STATUS_BLOCKED_INVENTORIED
    stories = {}
    for story, parsed_count, parent_count in (
            (STORY_HTTP, HTTP_PARSED_OBLIGATION_COUNT, HTTP_PARENT_OBLIGATION_COUNT),
            (STORY_CLI, CLI_PARSED_OBLIGATION_COUNT, CLI_PARENT_OBLIGATION_COUNT)):
        story_rows = [row for row in rows if row["story"] == story]
        story_counts = {status: sum(1 for row in story_rows if row["status"] == status)
                        for status in (STATUS_PASS, STATUS_FAIL, STATUS_NOT_RUN)}
        stories[story] = {
            "parsed_obligation_count": parsed_count,
            "parent_obligation_count": parent_count,
            "counts": story_counts,
            "status": (STATUS_PASS if story_counts[STATUS_PASS] == parsed_count
                       else STATUS_FAIL if story_counts[STATUS_FAIL] else STATUS_BLOCKED_INVENTORIED),
        }
    return {
        "contract": DENSITY_CONTRACT,
        "schema_version": 1,
        "parent_stories": [STORY_HTTP, STORY_CLI],
        "status": overall,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "counts": counts,
        "stories": stories,
        "obligations": rows,
        "source_revision": SOURCE_REVISION,
        "execution": (LOCAL_EXECUTION if tier == "local"
                      else "preprod-injected-observation-projection" if injected_preprod
                      else PREPROD_EXECUTION),
        "transport_admission": (
            "in-memory observations only; no CLI, network connection, HTTP transport, "
            "or credential is opened by this package"
        ),
        "environment_tier": tier,
        "upstream_roster": {
            STORY_HTTP: "6 green / 8 total (2 red groups: pause-missing, delete-missing)",
            STORY_CLI: "2 green / 4 total (2 red-by-design groups: conversation run and empty lookup)",
            "basis": UPSTREAM_COLOR_BASIS,
        },
        "dual_channel_envelopes": {
            STORY_HTTP: {
                "reused_command": LEGACY_DUAL_COMMANDS["http"],
                "reused_case_ids": list(LEGACY_HTTP_CASE_IDS),
                "retained_conclusion": (
                    "r8 preprod dual-channel-bindings-run PASS 4/4 public obligations "
                    "(bind-denied, list-before, target-empty, list-after); pause/delete stay blocked"
                ),
            },
            STORY_CLI: {
                "reused_command": LEGACY_DUAL_COMMANDS["cli"],
                "reused_case_ids": list(LEGACY_CLI_CASE_IDS),
                "retained_conclusion": (
                    "r8 preprod dual-channel-run BLOCKED 0/9 with ChannelCliRunnerError at "
                    "bind_disabled_bridge (credential_kind=human_cookie; mixed non-pure-JSON console; "
                    "state run.json authoritative)"
                ),
            },
        },
        "named_registrations": _registration_registry(),
        "preprod_execution_gap": dict(PREPROD_EXECUTION_GAP),
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
    }
