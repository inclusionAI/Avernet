"""Frozen-family alignment contract for the Wave-D3 density overlay.

Five ``edge_permission.sh`` parents (roster rows #32-#35/#39 of the
2026-10-31 snapshot) already run DUAL with recorded preprod PASS receipts
while their local projections PASS through the frozen families:

* the four write-domain parents ride ``edge_friend_mutation_case`` and the
  ``dual-edge-friend-mutation-run`` command face;
* the read parent ``test_ep_friend_connections_friend_request_and_list``
  rides the frozen read slice ``EDGE-FRIEND-CONNECTIONS.list`` /
  ``EDGE-FRIEND-CONNECTIONS.list-repeat`` through ``dual-edge-read-run``;
  its POST create stays outside the frozen slice and becomes the density
  WRITE_ANNEX leaf owning a mutation-family seam reference.

This module freezes the overlay vocabulary without touching any shared
face: the five density case rows (case id, parent, frozen requirement ids,
seam methods, roster counts), the fail-closed family alignment against the
frozen shared registries and the reviewed release denominator, and the
existing envelope projection the overlay must leave unchanged (read family
4 case keys, mutation family 7 release case keys, backlog family 2 case
keys; executor-migration rows stay committed as-is).

Degenerate envelope statuses are defined here so every arm and the case run
share one fail-close vocabulary:

* ``LOCAL_MODEL_CONFLICT`` — an owned-local pillar contradicted the frozen
  plan, the seam source, or the recorded owned-local round;
* ``PREPROD_RECEIPT_DRIFT`` — an attached recorded preprod receipt
  contradicted the frozen case identity or the honest NOT_RUN posture;
* ``BLOCKED_EVIDENCE_CONFLICT`` — an attached evidence root carried none of
  the family's recorded receipts.

The optional wrapper command ``dual-edge-permission-density-run`` is
declared describe-only; the registration delta lives in
:mod:`~bcs_e2e.edge_permission_backlog.density_registration_proposal` and
nothing in this subpackage applies it.
"""
from __future__ import annotations

from typing import Any

from ..edge_friend_mutation_case import (
    edge_friend_mutation_case_plan,
    edge_friend_mutation_source_binding,
)
from ..edge_friend_mutation_case_run import CASE_KEYS as MUTATION_FAMILY_CASE_KEYS
from ..edge_permission_backlog.case import CASE_KEYS as BACKLOG_CASE_KEYS
from ..edge_read_case import edge_read_case_plans, edge_read_public_aliases
from ..edge_read_case_run import CASE_KEYS as READ_FAMILY_CASE_KEYS
from ..edge_read_contract import (
    FRIEND_CONNECTIONS_PARENT,
    FRIEND_CONNECTIONS_REQUIREMENTS,
    friend_connections_list_source_binding,
)
from ..inventory import assert_reviewed_parent
from ..run_state import RunStateError
from .density_obligations import (
    ADDED_BY_PARENT,
    CASE_KEY_BY_PARENT,
    DENSITY_LEAF_COUNT,
    EXISTING_BY_PARENT,
    EXISTING_TOTAL,
    FROZEN_MUTATION_CASE_IDS,
    FROZEN_READ_CASE_ID,
    FROZEN_READ_PUBLIC_CASE_ID,
    FROZEN_READ_REQUIREMENTS,
    PARENTS,
    PARSED_BY_PARENT,
    PARSED_TOTAL,
    PARENT_BY_CASE_KEY,
    PROJECTED_SUPERSET_BY_PARENT,
)

SCHEMA_VERSION = 1
SUITE = "dual-edge-permission-density-v1"
REPORT_CONTRACT = "dual-edge-permission-density-case-run-v1"
BINDING_CONTRACT = "dual-edge-permission-density-binding-v1"
PROFILE_CONTRACT = "dual-edge-permission-density-profile-v1"
OPTIONAL_DENSITY_COMMAND = "dual-edge-permission-density-run"
OPTIONAL_COMMAND_REGISTRATION = "OPTIONAL_UNREGISTERED_DESCRIBE_ONLY"

LOCAL_MODEL_READY = "LOCAL_MODEL_READY"
LOCAL_MODEL_CONFLICT = "LOCAL_MODEL_CONFLICT"
PREPROD_NOT_RUN = "NOT_RUN"
REMOTE_VALIDATION = "NOT_RUN"
ENVELOPE_STATUS = "PASS_RECORDED_DENSITY_NOT_ENROLLED"
ENROLLMENT_NOT_ENROLLED = "NOT_ENROLLED"
REGISTRATION_STATE = "UNCHANGED_OFFLINE_PROJECTION"
DEGENERATE_LOCAL_MODEL_CONFLICT = "LOCAL_MODEL_CONFLICT"
DEGENERATE_PREPROD_RECEIPT_DRIFT = "PREPROD_RECEIPT_DRIFT"
DEGENERATE_BLOCKED_EVIDENCE_CONFLICT = "BLOCKED_EVIDENCE_CONFLICT"

MUTATION_FAMILY_KIND = "friend_mutation"
READ_FAMILY_KIND = "friend_read_slice_with_write_annex"
MUTATION_RUN_COMMAND = "dual-edge-friend-mutation-run"
READ_RUN_COMMAND = "dual-edge-read-run"
BACKLOG_RUN_COMMAND = "dual-edge-permission-backlog-run"
SOURCE_FAMILY_SUITE = "owned-local-edge-friend-mutations-v1"
READ_FAMILY_CONTRACT = "dual-edge-read-case-run-v1"
MUTATION_FAMILY_CONTRACT = "dual-edge-friend-mutation-case-run-v1"

ERROR_ADMISSION_CASE_KEY = "error-admission-branches"
CONNECT_PRIVATE_DISCOVER_CASE_KEY = "connect-private-discover"
FRIEND_REQUEST_AND_LIST_CASE_KEY = "friend-request-and-list"
ACCEPT_REJECT_CANCEL_CASE_KEY = "accept-reject-cancel"
MUTUAL_AUTO_APPROVE_CASE_KEY = "mutual-auto-approve"
DENSITY_CASE_KEYS = (
    ERROR_ADMISSION_CASE_KEY,
    CONNECT_PRIVATE_DISCOVER_CASE_KEY,
    FRIEND_REQUEST_AND_LIST_CASE_KEY,
    ACCEPT_REJECT_CANCEL_CASE_KEY,
    MUTUAL_AUTO_APPROVE_CASE_KEY,
)

# Roster counts verbatim from the 2026-10-31 snapshot
# (assertion-gap-matrix-20261031.md section A2 rows #32-#35/#39, identical
# to coverage-map-20261031.md section 2 rows).
DENSITY_CASE_ROWS: dict[str, dict[str, Any]] = {
    ERROR_ADMISSION_CASE_KEY: {
        "case_id": FROZEN_MUTATION_CASE_IDS["test_ep_error_and_admission_branches"],
        "public_case_id": None,
        "parent_id": PARENT_BY_CASE_KEY[ERROR_ADMISSION_CASE_KEY],
        "frozen_requirement_ids": ("EDGE_FRIEND_MUTATIONS.error-admission-branches",),
        "family_kind": MUTATION_FAMILY_KIND,
        "run_command": MUTATION_RUN_COMMAND,
        "seam_methods": ("run_error_admission_branches",),
        "roster": {
            "parsed": PARSED_BY_PARENT[PARENTS[0]],
            "frozen_existing": EXISTING_BY_PARENT[PARENTS[0]],
            "density_added": ADDED_BY_PARENT[PARENTS[0]],
            "projected_superset": PROJECTED_SUPERSET_BY_PARENT[PARENTS[0]],
        },
    },
    CONNECT_PRIVATE_DISCOVER_CASE_KEY: {
        "case_id": FROZEN_MUTATION_CASE_IDS["test_ep_connect_private_and_discover_branches"],
        "public_case_id": None,
        "parent_id": PARENT_BY_CASE_KEY[CONNECT_PRIVATE_DISCOVER_CASE_KEY],
        "frozen_requirement_ids": ("EDGE_FRIEND_MUTATIONS.connect-private-discover",),
        "family_kind": MUTATION_FAMILY_KIND,
        "run_command": MUTATION_RUN_COMMAND,
        "seam_methods": ("run_connect_private_discover",),
        "roster": {
            "parsed": PARSED_BY_PARENT[PARENTS[1]],
            "frozen_existing": EXISTING_BY_PARENT[PARENTS[1]],
            "density_added": ADDED_BY_PARENT[PARENTS[1]],
            "projected_superset": PROJECTED_SUPERSET_BY_PARENT[PARENTS[1]],
        },
    },
    FRIEND_REQUEST_AND_LIST_CASE_KEY: {
        "case_id": FROZEN_READ_CASE_ID,
        "public_case_id": FROZEN_READ_PUBLIC_CASE_ID,
        "parent_id": PARENT_BY_CASE_KEY[FRIEND_REQUEST_AND_LIST_CASE_KEY],
        "frozen_requirement_ids": FROZEN_READ_REQUIREMENTS,
        "family_kind": READ_FAMILY_KIND,
        "run_command": READ_RUN_COMMAND,
        "seam_methods": ("read_friend_connections_list",
                         "read_friend_connections_list_repeat"),
        "annex_seam_method": "_create_request",
        "roster": {
            "parsed": PARSED_BY_PARENT[PARENTS[2]],
            "frozen_existing": EXISTING_BY_PARENT[PARENTS[2]],
            "density_added": ADDED_BY_PARENT[PARENTS[2]],
            "projected_superset": PROJECTED_SUPERSET_BY_PARENT[PARENTS[2]],
        },
    },
    ACCEPT_REJECT_CANCEL_CASE_KEY: {
        "case_id": FROZEN_MUTATION_CASE_IDS["test_ep_friend_connections_friend_accept_reject_cancel"],
        "public_case_id": None,
        "parent_id": PARENT_BY_CASE_KEY[ACCEPT_REJECT_CANCEL_CASE_KEY],
        "frozen_requirement_ids": ("EDGE_FRIEND_MUTATIONS.accept-reject-cancel",),
        "family_kind": MUTATION_FAMILY_KIND,
        "run_command": MUTATION_RUN_COMMAND,
        "seam_methods": ("run_accept_reject_cancel",),
        "roster": {
            "parsed": PARSED_BY_PARENT[PARENTS[3]],
            "frozen_existing": EXISTING_BY_PARENT[PARENTS[3]],
            "density_added": ADDED_BY_PARENT[PARENTS[3]],
            "projected_superset": PROJECTED_SUPERSET_BY_PARENT[PARENTS[3]],
        },
    },
    MUTUAL_AUTO_APPROVE_CASE_KEY: {
        "case_id": FROZEN_MUTATION_CASE_IDS["test_ep_friend_connections_mutual_auto_approve"],
        "public_case_id": None,
        "parent_id": PARENT_BY_CASE_KEY[MUTUAL_AUTO_APPROVE_CASE_KEY],
        "frozen_requirement_ids": ("EDGE_FRIEND_MUTATIONS.mutual-auto-approve",),
        "family_kind": MUTATION_FAMILY_KIND,
        "run_command": MUTATION_RUN_COMMAND,
        "seam_methods": ("run_mutual_auto_approve",),
        "roster": {
            "parsed": PARSED_BY_PARENT[PARENTS[4]],
            "frozen_existing": EXISTING_BY_PARENT[PARENTS[4]],
            "density_added": ADDED_BY_PARENT[PARENTS[4]],
            "projected_superset": PROJECTED_SUPERSET_BY_PARENT[PARENTS[4]],
        },
    },
}


def density_case_row(case_key: str) -> dict[str, Any]:
    """Return one frozen density case row, fail-closed on drift."""
    if case_key not in DENSITY_CASE_ROWS:
        raise RunStateError(
            "unknown edge_permission density case key: " + str(case_key))
    row = dict(DENSITY_CASE_ROWS[case_key])
    row["case_key"] = case_key
    roster = row["roster"]
    if roster["frozen_existing"] + roster["density_added"] != roster["parsed"]:
        raise RunStateError(
            "density roster drifted for " + case_key + ": existing + density"
            " no longer equals the parsed snapshot")
    return row


def existing_envelope_projection() -> dict[str, Any]:
    """Project the existing dual envelopes the overlay must not change."""
    backlog_keys = tuple(BACKLOG_CASE_KEYS)
    release_keys = [key for key in MUTATION_FAMILY_CASE_KEYS
                    if key not in backlog_keys]
    if len(release_keys) != 7 or len(READ_FAMILY_CASE_KEYS) != 4:
        raise RunStateError(
            "existing dual envelope families drifted from the committed"
            " registration baseline (read 4 / mutation release 7 / backlog 2)")
    return {
        "registration_state": REGISTRATION_STATE,
        "read_family": {
            "command": READ_RUN_COMMAND,
            "case_keys": list(READ_FAMILY_CASE_KEYS),
            "case_key_count": len(READ_FAMILY_CASE_KEYS),
            "receipt_contract": READ_FAMILY_CONTRACT,
        },
        "mutation_family": {
            "command": MUTATION_RUN_COMMAND,
            "case_keys": release_keys,
            "case_key_count": len(release_keys),
            "receipt_contract": MUTATION_FAMILY_CONTRACT,
        },
        "backlog_family": {
            "command": BACKLOG_RUN_COMMAND,
            "case_keys": list(backlog_keys),
            "case_key_count": len(backlog_keys),
            "receipt_contract": "dual-edge-permission-backlog-case-run-v1",
        },
        "density_overlay": {
            "suite": SUITE,
            "case_key_count": len(DENSITY_CASE_KEYS),
            "density_leaves": DENSITY_LEAF_COUNT,
            "optional_command": OPTIONAL_DENSITY_COMMAND,
            "optional_command_registration": OPTIONAL_COMMAND_REGISTRATION,
        },
        "basis": (
            "counts are read at runtime from the frozen family registries; "
            "this overlay changes no shared face file and no committed "
            "executor-migration row"),
    }


def assert_family_alignment() -> bool:
    """Fail closed when the overlay drifts from the frozen shared families."""
    expected_keys = tuple(CASE_KEY_BY_PARENT[parent] for parent in PARENTS)
    if tuple(DENSITY_CASE_KEYS) != expected_keys:
        raise RunStateError("density case keys drifted from the roster parents")
    if DENSITY_LEAF_COUNT != 22 or EXISTING_TOTAL != 6 or PARSED_TOTAL != 28:
        raise RunStateError("density roster totals drifted from the snapshot")
    aliases = edge_read_public_aliases()
    for case_key in DENSITY_CASE_KEYS:
        row = density_case_row(case_key)
        assert_reviewed_parent(row["parent_id"],
                               family="dual-edge-permission-density")
        if row["family_kind"] == MUTATION_FAMILY_KIND:
            plan = edge_friend_mutation_case_plan(row["case_id"])
            if plan.case_id != row["case_id"] or plan.parent_id != row["parent_id"]:
                raise RunStateError(
                    "density wrapper drifted from the frozen mutation plan: "
                    + case_key)
            requirement_ids = tuple(item.requirement_id
                                    for item in plan.requirements)
            if requirement_ids != row["frozen_requirement_ids"]:
                raise RunStateError(
                    "density wrapper drifted from the frozen mutation"
                    " requirement ids: " + case_key)
            if plan.requirements[0].adapter_method not in row["seam_methods"]:
                raise RunStateError(
                    "density wrapper drifted from the frozen mutation seam:"
                    " " + case_key)
            binding = edge_friend_mutation_source_binding(row["case_id"])
            if binding["parent"] != row["parent_id"]:
                raise RunStateError(
                    "mutation source binding parent drifted: " + case_key)
            if binding["source_revision"] != binding["sources"]["edge_permission.sh"]:
                raise RunStateError(
                    "mutation source binding digest is not self-consistent: "
                    + case_key)
        else:
            plans = edge_read_case_plans()
            plan = next((item for item in plans
                         if item.case_id == row["case_id"]), None)
            if plan is None:
                raise RunStateError(
                    "frozen read plan no longer carries the density read slice")
            if plan.requirement_ids != row["frozen_requirement_ids"]:
                raise RunStateError(
                    "density wrapper drifted from the frozen read requirement"
                    " ids: " + case_key)
            if plan.parent_id != row["parent_id"]:
                raise RunStateError(
                    "density wrapper drifted from the frozen read parent: "
                    + case_key)
            for requirement_id, method in zip(
                    plan.requirement_ids, row["seam_methods"]):
                if plan.adapters[requirement_id] != method:
                    raise RunStateError(
                        "density wrapper drifted from the frozen read seam: "
                        + case_key)
            if aliases.get(row["public_case_id"]) != row["case_id"]:
                raise RunStateError(
                    "density wrapper drifted from the frozen public alias: "
                    + case_key)
            binding = friend_connections_list_source_binding()
            if binding["parent"] != FRIEND_CONNECTIONS_PARENT:
                raise RunStateError(
                    "read source binding parent drifted from the frozen map")
            if tuple(item["id"] for item in binding["requirements"]) != FROZEN_READ_REQUIREMENTS:
                raise RunStateError(
                    "read source binding requirements drifted from the frozen"
                    " map")
    projection = existing_envelope_projection()
    total = (projection["read_family"]["case_key_count"]
             + projection["mutation_family"]["case_key_count"]
             + projection["backlog_family"]["case_key_count"])
    if total != 13:
        raise RunStateError("existing envelope projection totals drifted")
    return True
