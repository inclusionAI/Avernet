"""Source-anchored density obligations for the five Wave-D3 edge_permission parents.

Wave-D3 backfills assertion density for five upstream ``edge_permission.sh``
parents whose local projections already PASS.  Density is an obligation SUPERSET
overlay: the frozen families (``edge_read_contract`` for the friend-request-and-
list read slice, ``edge_friend_mutation_case`` for the four mutation parents)
keep their executed obligations untouched; this module ports each upstream
assertion site that the roster parsed but no local obligation leaf yet carries.

Conventions (mirroring ``bcs_e2e.delivery_density`` and the read-family
contracts):

* every density leaf anchors to its ``edge_permission.sh`` assertion line and
  driving request line(s) with the exact upstream quote; anchor drift fails
  closed with ``RunStateError`` at build time;
* upstream has no native assertion ids, so each leaf carries a ``note`` with
  its assert/request line anchors; the parsed roster counts come from the
  2026-10-31 snapshot (``assertion-gap-matrix-20261031.md`` A2 rows #32-#35/#39,
  identical in ``coverage-map-20261031.md`` section 2) and are reconciled per
  parent as existing + added == parsed;
* the 22 leaves close the roster exactly: frozen 6 + density 22 == parsed 28;
* a request line may feed two leaves (the mutual accept POST anchors both the
  decided-row leaf and the both-edges leaf); charged membership, not partition,
  is the guard invariant;
* shell sites without assertion credit (fixture writes, the silent duplicate
  warm-up, fake-id degrade probes, warn-only journey probes, completion passes)
  are disclosed in ``NOT_CHARGED_SOURCE_SITES`` and never silently counted;
* the completeness guard fails closed unless every scanned
  ``_api_authed``/``api_get``/``api_put``/``_set_protected_for_friend_request``/
  ``_restore_public_visibility`` line in the five function bodies is classified
  as a charged leaf request line, the frozen read slice, or a disclosed site;
* three owned-local documented corrections keep the frozen family's canonical
  semantics (duplicate-create idempotence, private-create exact 403, the
  organization-discovery fail-closed membership branch); the upstream
  historical text travels alongside as ``upstream_expectation``.

No transport, no credential resolution.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..run_state import RunStateError

SOURCE_FILE = "edge_permission.sh"
SOURCE_RELPATH = "apps/bcs/scripts/e2e-test/edge_permission.sh"
SOURCE_PATH = Path(__file__).resolve().parents[3] / "e2e-test" / SOURCE_FILE

PARENTS = (
    "test_ep_error_and_admission_branches",
    "test_ep_connect_private_and_discover_branches",
    "test_ep_friend_connections_friend_request_and_list",
    "test_ep_friend_connections_friend_accept_reject_cancel",
    "test_ep_friend_connections_mutual_auto_approve",
)

PARENT_BY_CASE_KEY = {
    "error-admission-branches": PARENTS[0],
    "connect-private-discover": PARENTS[1],
    "friend-request-and-list": PARENTS[2],
    "accept-reject-cancel": PARENTS[3],
    "mutual-auto-approve": PARENTS[4],
}
CASE_KEY_BY_PARENT = {parent: key for key, parent in PARENT_BY_CASE_KEY.items()}

# Roster snapshot parsed counts (assertion-gap-matrix-20261031.md A2; the
# coverage-map-20261031.md section 2 rows carry the same values).
PARSED_BY_PARENT = {
    "test_ep_error_and_admission_branches": 7,
    "test_ep_connect_private_and_discover_branches": 8,
    "test_ep_friend_connections_friend_request_and_list": 3,
    "test_ep_friend_connections_friend_accept_reject_cancel": 2,
    "test_ep_friend_connections_mutual_auto_approve": 8,
}
EXISTING_BY_PARENT = {
    "test_ep_error_and_admission_branches": 1,
    "test_ep_connect_private_and_discover_branches": 1,
    "test_ep_friend_connections_friend_request_and_list": 2,
    "test_ep_friend_connections_friend_accept_reject_cancel": 1,
    "test_ep_friend_connections_mutual_auto_approve": 1,
}
ADDED_BY_PARENT = {
    "test_ep_error_and_admission_branches": 6,
    "test_ep_connect_private_and_discover_branches": 7,
    "test_ep_friend_connections_friend_request_and_list": 1,
    "test_ep_friend_connections_friend_accept_reject_cancel": 1,
    "test_ep_friend_connections_mutual_auto_approve": 7,
}
DENSITY_LEAF_COUNT = sum(ADDED_BY_PARENT.values())
EXISTING_TOTAL = sum(EXISTING_BY_PARENT.values())
PARSED_TOTAL = sum(PARSED_BY_PARENT.values())
PROJECTED_SUPERSET_BY_PARENT = {
    parent: EXISTING_BY_PARENT[parent] + ADDED_BY_PARENT[parent]
    for parent in PARENTS
}

FROZEN_MUTATION_CASE_IDS = {
    "test_ep_error_and_admission_branches": "LOCAL-EDGE-ERROR-ADMISSION-BRANCHES-01",
    "test_ep_connect_private_and_discover_branches": "LOCAL-EDGE-CONNECT-PRIVATE-DISCOVER-01",
    "test_ep_friend_connections_friend_accept_reject_cancel": "LOCAL-EDGE-FRIEND-ACCEPT-REJECT-CANCEL-01",
    "test_ep_friend_connections_mutual_auto_approve": "LOCAL-EDGE-FRIEND-MUTUAL-AUTO-APPROVE-01",
}
FROZEN_READ_CASE_ID = "LOCAL-EDGE-FRIEND-CONNECTIONS-LIST-01"
FROZEN_READ_PUBLIC_CASE_ID = "PUBLIC-EDGE-FRIEND-CONNECTIONS-LIST-01"
FROZEN_READ_REQUIREMENTS = ("EDGE-FRIEND-CONNECTIONS.list", "EDGE-FRIEND-CONNECTIONS.list-repeat")

FROZEN_READ_SLICE_SITES = (
    {"parent": "test_ep_friend_connections_friend_request_and_list",
     "requirement_id": "EDGE-FRIEND-CONNECTIONS.list", "line": 138,
     "quote": '_api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot"'},
    {"parent": "test_ep_friend_connections_friend_request_and_list",
     "requirement_id": "EDGE-FRIEND-CONNECTIONS.list-repeat", "line": 147,
     "quote": '_api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot"'},
)

_TRANSPORT_RE = re.compile(r'^\s*(?:_api_authed|api_get|api_put)\s+"')
_FIXTURE_WRITE_RE = re.compile(r'^\s*(?:_set_protected_for_friend_request|_restore_public_visibility)\s+"')

# Sites without upstream assertion credit, disclosed so no report silently
# loses them.  classification: fixture_write | frozen_journey_seam |
# historical_degrade_probe | silent_warmup | journey_completion (the last may
# be a non-scanned journey completion ``pass`` line).
NOT_CHARGED_SOURCE_SITES: tuple[dict[str, Any], ...] = (
    {"parent": "test_ep_friend_connections_friend_request_and_list", "lines": (125, 158),
     "classification": "fixture_write",
     "reason": "ENG fixture visibility writes feeding the read slice; the owned-local mutation fixture seam owns them"},
    {"parent": "test_ep_friend_connections_friend_accept_reject_cancel", "lines": (174, 265),
     "classification": "fixture_write",
     "reason": "PM fixture visibility writes; the owned-local mutation fixture seam owns them"},
    {"parent": "test_ep_friend_connections_friend_accept_reject_cancel", "lines": (185, 215, 249),
     "classification": "frozen_journey_seam",
     "reason": "decision POSTs asserted by the frozen EDGE_FRIEND_MUTATIONS.accept-reject-cancel journey seam against real pending rows"},
    {"parent": "test_ep_friend_connections_friend_accept_reject_cancel", "lines": (195, 199),
     "classification": "frozen_journey_seam",
     "reason": "warn-only post-accept friend-list and admission probes asserted by the frozen journey seam with real data"},
    {"parent": "test_ep_friend_connections_friend_accept_reject_cancel", "lines": (204, 225, 259),
     "classification": "historical_degrade_probe",
     "reason": "historical fake-id degrade probes; the frozen family documents the correction against real pending rows"},
    {"parent": "test_ep_friend_connections_mutual_auto_approve", "lines": (416, 417, 455, 456),
     "classification": "fixture_write",
     "reason": "CEO/ENG fixture visibility writes; the owned-local mutation fixture seam owns them"},
    {"parent": "test_ep_friend_connections_mutual_auto_approve", "lines": (458,),
     "classification": "journey_completion",
     "reason": "journey completion pass represented by the frozen EDGE_FRIEND_MUTATIONS.mutual-auto-approve umbrella obligation"},
    {"parent": "test_ep_error_and_admission_branches", "lines": (551, 558, 573, 577),
     "classification": "fixture_write",
     "reason": "PM fixture visibility writes around the no-edge and duplicate branches; the owned-local mutation fixture seam owns them"},
    {"parent": "test_ep_error_and_admission_branches", "lines": (574,),
     "classification": "silent_warmup",
     "reason": "silent duplicate-branch warm-up create (stdout discarded); the charged duplicate site is line 575"},
    {"parent": "test_ep_error_and_admission_branches", "lines": (580,),
     "classification": "journey_completion",
     "reason": "journey completion pass represented by the frozen EDGE_FRIEND_MUTATIONS.error-admission-branches umbrella obligation"},
    {"parent": "test_ep_connect_private_and_discover_branches", "lines": (626,),
     "classification": "journey_completion",
     "reason": "journey completion pass represented by the frozen EDGE_FRIEND_MUTATIONS.connect-private-discover umbrella obligation"},
)

REQUIRED_BODY_LITERALS = {
    "test_ep_error_and_admission_branches": (
        "(BotNotFound branch)", "(NoEdge branch)", "actor_kind=human",
        "(expect 400)", "(expect 404)", "(expect 409)"),
    "test_ep_connect_private_and_discover_branches": (
        '{"visibility":"private"}', "(expect private-collab error)",
        "collaborate_bot=$BOT_PM_UUID", "role=contributor", "organization_code=DEMO"),
    "test_ep_friend_connections_friend_request_and_list": (
        '/collaboration/friend-connections/requests',
        "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot", "returns 200"),
    "test_ep_friend_connections_friend_accept_reject_cancel": (
        "direction=received", "direction=sent", "/accept", "/reject", "/cancel"),
    "test_ep_friend_connections_mutual_auto_approve": (
        "direction=received", "direction=all", "/accept", "reverse auto-approved, 2 edges built"),
}

RELATION_REFINES = "REFINES_FROZEN_OBLIGATION"
RELATION_NET_NEW = "NET_NEW_GRANULARITY"
RELATION_WRITE_ANNEX = "WRITE_ANNEX"
RELATION_FIXTURE_WRITE = "FIXTURE_WRITE"
RELATIONS = (RELATION_REFINES, RELATION_NET_NEW, RELATION_WRITE_ANNEX, RELATION_FIXTURE_WRITE)

_SEAM_MUTATIONS = "bcs_e2e.local_edge_friend_mutations_case"
_SEAM_READ = "bcs_e2e.owned_edge_read_adapter"


def _leaf(*, obligation_id, case_key, relation, family, kind, method, path, actor,
          evaluator_key, expectation, assertion_line, assertion_quote,
          request_lines, request_quotes, seam_module, seam_method, seam_literals,
          recorded_keys, upstream_expectation=None, note=None):
    return {
        "obligation_id": obligation_id, "case_key": case_key, "parent_id": PARENT_BY_CASE_KEY[case_key],
        "relation": relation, "family": family, "kind": kind, "method": method, "path": path,
        "actor": actor, "evaluator_key": evaluator_key, "expectation": expectation,
        "assertion_line": assertion_line, "assertion_quote": assertion_quote,
        "request_lines": tuple(request_lines), "request_quotes": tuple(request_quotes),
        "seam": {"module": seam_module, "method": seam_method, "literals": tuple(seam_literals)},
        "recorded_keys": tuple(recorded_keys), "upstream_expectation": upstream_expectation,
        "note": note,
    }


_ERROR_ADMISSION_LEAVES = (
    _leaf(
        obligation_id="EDGE-ERROR-ADMISSION.bot-not-found-admission",
        case_key="error-admission-branches", relation=RELATION_REFINES,
        family="EDGE-ERROR-ADMISSION.bot-not-found-admission",
        kind="http", method="GET",
        path="/bots/00000000-0000-0000-0000-000000000000/admission?actor=$BOT_CEO_UUID",
        actor="UNAUTHENTICATED(service-admission)",
        evaluator_key="admission_branches.unknown_bot",
        expectation="unknown-bot admission is refused with reason_code bot_not_found (BotNotFound branch)",
        assertion_line=547, assertion_quote="admission unknown bot: status=$HTTP_STATUS (BotNotFound branch)",
        request_lines=(546,),
        request_quotes=('_api_authed "GET" "/bots/00000000-0000-0000-0000-000000000000/admission?actor=$BOT_CEO_UUID',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_error_admission_branches",
        seam_literals=("the unknown-bot admission did not report bot_not_found",),
        recorded_keys=("admission_branches", "unknown_bot"),
        upstream_expectation="upstream warns the BotNotFound status without a machine-checkable code; the owned-local seam pins reason_code=bot_not_found",
        note="site a547/r546"),
    _leaf(
        obligation_id="EDGE-ERROR-ADMISSION.protected-no-edge",
        case_key="error-admission-branches", relation=RELATION_REFINES,
        family="EDGE-ERROR-ADMISSION.protected-no-edge",
        kind="http", method="GET", path="/bots/$BOT_PM_UUID/admission?actor=$BOT_CEO_UUID",
        actor="CEO_BOT(query-param)", evaluator_key="admission_branches.protected_no_edge",
        expectation="admission for a protected bot, unrelated actor, no edge: reason_code no_edge (NoEdge branch)",
        assertion_line=553, assertion_quote="admission protected-no-edge: status=$HTTP_STATUS (NoEdge branch)",
        request_lines=(552,),
        request_quotes=('_api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=$BOT_CEO_UUID',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_error_admission_branches",
        seam_literals=("the protected no-edge admission did not report no_edge",),
        recorded_keys=("admission_branches", "protected_no_edge"), note="site a553/r552"),
    _leaf(
        obligation_id="EDGE-ERROR-ADMISSION.actor-kind-human-form",
        case_key="error-admission-branches", relation=RELATION_NET_NEW,
        family="EDGE-ERROR-ADMISSION.actor-kind-human-form",
        kind="http", method="GET", path="/bots/$BOT_PM_UUID/admission?actor=human_88001&actor_kind=human",
        actor="HUMAN_88001(query-param)", evaluator_key="admission_branches.protected_no_edge_human",
        expectation="the actor_kind=human query form on a protected no-edge bot is refused no_edge, never authorized",
        assertion_line=556, assertion_quote="admission protected human actor: status=$HTTP_STATUS",
        request_lines=(555,),
        request_quotes=('_api_authed "GET" "/bots/$BOT_PM_UUID/admission?actor=human_88001&actor_kind=human',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_error_admission_branches",
        seam_literals=("the human-actor protected admission was authorized",),
        recorded_keys=("admission_branches", "protected_no_edge_human"),
        upstream_expectation="upstream only warns the status; the owned-local seam pins no_edge for the human actor form",
        note="site a556/r555; net-new granularity: the frozen umbrella expectation does not enumerate the human actor_kind form"),
    _leaf(
        obligation_id="EDGE-ERROR-ADMISSION.self-add-rejected",
        case_key="error-admission-branches", relation=RELATION_REFINES,
        family="EDGE-ERROR-ADMISSION.self-add-rejected",
        kind="http", method="POST", path="/collaboration/friend-connections/requests",
        actor="CEO_BOT(bearer)", evaluator_key="self_add",
        expectation="self-add create (to_bot == caller) is rejected 400 with error_code cannot_add_self",
        assertion_line=563, assertion_quote="self-add friend request: status=$HTTP_STATUS (expect 400)",
        request_lines=(562,),
        request_quotes=('_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_CEO_UUID',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_error_admission_branches",
        seam_literals=("exact_status=400",), recorded_keys=("self_add", "http_status"),
        note="site a563/r562; the seam pins exact wire status 400 plus a non-empty error body",
    ),
    _leaf(
        obligation_id="EDGE-ERROR-ADMISSION.unknown-target-rejected",
        case_key="error-admission-branches", relation=RELATION_REFINES,
        family="EDGE-ERROR-ADMISSION.unknown-target-rejected",
        kind="http", method="POST", path="/collaboration/friend-connections/requests",
        actor="CEO_BOT(bearer)", evaluator_key="unknown_bot_create",
        expectation="create with an unknown to_bot uuid is rejected 404 with error_code bot_not_found",
        assertion_line=567, assertion_quote="friend request unknown target: status=$HTTP_STATUS (expect 404)",
        request_lines=(566,),
        request_quotes=('_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"00000000',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_error_admission_branches",
        seam_literals=("exact_status=404",), recorded_keys=("unknown_bot", "http_status"),
        note="site a567/r566; the seam pins exact wire status 404 plus a non-empty error body",
    ),
    _leaf(
        obligation_id="EDGE-ERROR-ADMISSION.duplicate-create-idempotent",
        case_key="error-admission-branches", relation=RELATION_REFINES,
        family="EDGE-ERROR-ADMISSION.duplicate-create-idempotent",
        kind="http", method="POST", path="/collaboration/friend-connections/requests",
        actor="CEO_BOT(bearer)", evaluator_key="duplicate",
        expectation="duplicate same-direction create over a protected pending target is the idempotent pending result over the same request row (one pending row, same request_ids)",
        assertion_line=576, assertion_quote="duplicate pending friend request: status=$HTTP_STATUS (expect 409)",
        request_lines=(575,),
        request_quotes=('_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_PM_UUID',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_error_admission_branches",
        seam_literals=("the duplicate same-direction create is not the idempotent pending result over the same request row",),
        recorded_keys=("duplicate_pending_count", "duplicate_request_ids"),
        upstream_expectation="upstream history expects 409 PendingRequestExists; the owned-local family documents the idempotent pending result as canonical",
        note=("site a576/r575; warm-up r574 disclosed not-charged; r5 dual leg kept one pending row "
              "but echoed two distinct request ids (drift DUP-CREATE-ID-ECHO disclosed by the evaluator)"),
    ),
)

_CONNECT_DISCOVER_LEAVES = (
    _leaf(
        obligation_id="EDGE-CONNECT-DISCOVER.owner-visibility-private",
        case_key="connect-private-discover", relation=RELATION_FIXTURE_WRITE,
        family="EDGE-CONNECT-DISCOVER.owner-visibility-private",
        kind="fixture-write", method="PUT", path="/bots/$BOT_PM_UUID/visibility",
        actor="BOT_PM_OWNER(mock-header)", evaluator_key="visibility_writes.private",
        expectation="owner PM visibility write to private succeeds and feeds the private-target branch; dual receipts do not journal visibility writes, so the leaf rides the fixture seam (disclosed)",
        assertion_line=603, assertion_quote="set PM private (owner): status=$HTTP_STATUS",
        request_lines=(602,),
        request_quotes=('api_put "/bots/$BOT_PM_UUID/visibility" \'{"visibility":"private"}\'',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_connect_private_discover",
        seam_literals=("_set_visibility('PM', 'private')",),
        recorded_keys=("visibility_writes", "private"),
        upstream_expectation="upstream warns the owner write status",
        note="site a603/r602; material gap: dual receipts carry no visibility-write journal"),
    _leaf(
        obligation_id="EDGE-CONNECT-DISCOVER.private-target-create-rejected",
        case_key="connect-private-discover", relation=RELATION_REFINES,
        family="EDGE-CONNECT-DISCOVER.private-target-create-rejected",
        kind="http", method="POST", path="/collaboration/friend-connections/requests",
        actor="CEO_BOT(bearer)", evaluator_key="private_create",
        expectation="create against a private target is rejected 403 (PrivateBotCannotCollaborate); preprod r5 answered 500 internal_error (defect PD-PRIVATE-CREATE-500 on file)",
        assertion_line=605, assertion_quote="friend request to private bot: status=$HTTP_STATUS (expect private-collab error)",
        request_lines=(604,),
        request_quotes=('_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_PM_UUID',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_connect_private_discover",
        seam_literals=("exact_status=403",), recorded_keys=("private_create_defect",),
        upstream_expectation="upstream history warns '(expect private-collab error)'; the owned-local family pins exact 403; r5/r6 preprod observed PD-PRIVATE-CREATE-500",
        note="site a605/r604"),
    _leaf(
        obligation_id="EDGE-CONNECT-DISCOVER.discover-private-empty",
        case_key="connect-private-discover", relation=RELATION_REFINES,
        family="EDGE-CONNECT-DISCOVER.discover-private-empty",
        kind="http", method="GET", path="/bots/discover?collaborate_bot=$BOT_PM_UUID",
        actor="GATEWAY_DRIVER(mock-human-header)", evaluator_key="private_discovery",
        expectation="discovery with a private collaborate_bot yields an empty result (collaborate_bot_is_private branch, count 0)",
        assertion_line=609, assertion_quote="discover collaborate_bot=private: status=$HTTP_STATUS",
        request_lines=(608,),
        request_quotes=('api_get "/bots/discover?collaborate_bot=$BOT_PM_UUID"',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_connect_private_discover",
        seam_literals=("the private collaborate_bot discovery did not yield an empty result",),
        recorded_keys=("private_discovery_empty",), note="site a609/r608"),
    _leaf(
        obligation_id="EDGE-CONNECT-DISCOVER.owner-visibility-restore-public",
        case_key="connect-private-discover", relation=RELATION_FIXTURE_WRITE,
        family="EDGE-CONNECT-DISCOVER.owner-visibility-restore-public",
        kind="fixture-write", method="PUT", path="/bots/$BOT_PM_UUID/visibility",
        actor="BOT_PM_OWNER(mock-header)", evaluator_key="visibility_writes.public",
        expectation="owner PM visibility restore to public succeeds, leaving the fixture clean; dual receipts do not journal visibility writes (disclosed)",
        assertion_line=613, assertion_quote="restore PM public: status=$HTTP_STATUS",
        request_lines=(612,),
        request_quotes=('api_put "/bots/$BOT_PM_UUID/visibility" \'{"visibility":"public"}\'',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_connect_private_discover",
        seam_literals=("_set_visibility('PM', 'public')",),
        recorded_keys=("visibility_writes", "public"),
        upstream_expectation="upstream warns the restore status",
        note="site a613/r612; material gap: dual receipts carry no visibility-write journal"),
    _leaf(
        obligation_id="EDGE-CONNECT-DISCOVER.discover-public-rows",
        case_key="connect-private-discover", relation=RELATION_REFINES,
        family="EDGE-CONNECT-DISCOVER.discover-public-rows",
        kind="http", method="GET", path="/bots/discover?collaborate_bot=$BOT_PM_UUID",
        actor="GATEWAY_DRIVER(mock-human-header)", evaluator_key="public_discovery",
        expectation="public collaborate_bot discovery returns the friend-list-for-collaboration rows: count agrees with entries length, requester self-excluded, entries carry bot_uuid/visibility/is_friend shape",
        assertion_line=615, assertion_quote="discover collaborate_bot=public: status=$HTTP_STATUS",
        request_lines=(614,),
        request_quotes=('api_get "/bots/discover?collaborate_bot=$BOT_PM_UUID"',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_connect_private_discover",
        seam_literals=("the public discovery result disagrees with its count",
                       "the public collaborate_bot discovery lists the requester itself"),
        recorded_keys=("public_discovery_count", "public_discovery_self_excluded", "public_directory_driver_listed"),
        upstream_expectation="upstream warns only the discovery status; the owned-local seam pins count/entry/self-exclusion shape",
        note=("site a615/r614; r5 journals count=35, self-exclusion, directory-driver listing; per-entry shape "
              "and is_friend counts are not journaled -> MATCH_PARTIAL")),
    _leaf(
        obligation_id="EDGE-CONNECT-DISCOVER.discover-role-requires-organization",
        case_key="connect-private-discover", relation=RELATION_REFINES,
        family="EDGE-CONNECT-DISCOVER.discover-role-requires-organization",
        kind="http", method="GET", path="/bots/discover?role=contributor",
        actor="GATEWAY_DRIVER(mock-human-header)", evaluator_key="role_branch",
        expectation="discover with role=contributor and no organization_code is rejected 400 carrying role_requires_organization_code",
        assertion_line=620, assertion_quote="discover role w/o org_code: status=$HTTP_STATUS (expect role_requires_organization_code)",
        request_lines=(619,),
        request_quotes=('api_get "/bots/discover?role=contributor"',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_connect_private_discover",
        seam_literals=("role without organization_code was not rejected 400 role_requires_organization_code",),
        recorded_keys=("role_no_org_drift",),
        note=("site a620/r619; r5 observed 400 invalid_operation whose message preserves "
              "role_requires_organization_code (PD-DISCOVER-BRANCH-DRIFT wrapper note; canonical code intact -> MATCH)")),
    _leaf(
        obligation_id="EDGE-CONNECT-DISCOVER.discover-organization-fail-closed",
        case_key="connect-private-discover", relation=RELATION_REFINES,
        family="EDGE-CONNECT-DISCOVER.discover-organization-fail-closed",
        kind="http", method="GET", path="/bots/discover?organization_code=DEMO",
        actor="GATEWAY_DRIVER(mock-human-header)", evaluator_key="organization_branch",
        expectation="organization_code discovery without a requester bot fails closed with an error body (membership enforced for an organization caller)",
        assertion_line=624, assertion_quote="discover org_code w/o requester: status=$HTTP_STATUS (expect 400)",
        request_lines=(623,),
        request_quotes=('api_get "/bots/discover?organization_code=DEMO"',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_connect_private_discover",
        seam_literals=("organization_code discovery for a non-member caller did not fail closed with an error body",),
        recorded_keys=("organization_no_requester_drift",),
        upstream_expectation="upstream history expects the 400 handler bad-request guard; preprod r5 answered 403 forbidden with the fail-closed body",
        note="site a624/r623; classifier DRIFT_DISCLOSED on the 403-vs-400 code drift, fail-closed body preserved"),
)

_FRIEND_REQUEST_AND_LIST_LEAVES = (
    _leaf(
        obligation_id="EDGE-FRIEND-CONNECTIONS.create-request",
        case_key="friend-request-and-list", relation=RELATION_WRITE_ANNEX,
        family="EDGE-FRIEND-CONNECTIONS.create-request",
        kind="http", method="POST", path="/collaboration/friend-connections/requests",
        actor="CEO_BOT(bearer)", evaluator_key="create_request_annex",
        expectation="the CEO->ENG create returns 200 with non-empty request_ids; annexed to the owned-local mutation seam, grants no completion credit to the frozen read slice",
        assertion_line=130, assertion_quote="collaboration/friend-connections/requests returns 200",
        request_lines=(128,),
        request_quotes=('_api_authed "POST" "/collaboration/friend-connections/requests"',),
        seam_module=_SEAM_MUTATIONS, seam_method="_create_request",
        seam_literals=("friend-connection create returned no request_ids",
                       "friend-connection create returned an unknown status"),
        recorded_keys=("cross_family_create_rows",),
        upstream_expectation="upstream counts a pass on 200 and equally on its warn branch (both arms increment TESTS_PASSED); the annex keeps the write outside the frozen read slice",
        note=("site a130/r128; WRITE_ANNEX verified locally through the frozen mutation family "
              "_create_request seam; preprod enrollment stays NOT_ENROLLED; r5 mutation-round create "
              "rows over the same endpoint are cited as adjacent evidence only"),
    ),
)

_ACCEPT_REJECT_CANCEL_LEAVES = (
    _leaf(
        obligation_id="EDGE-ACCEPT-REJECT-CANCEL.pending-row-pre-decision-visibility",
        case_key="accept-reject-cancel", relation=RELATION_NET_NEW,
        family="EDGE-ACCEPT-REJECT-CANCEL.pending-row-pre-decision-visibility",
        kind="http", method="GET",
        path="/collaboration/friend-connections/requests?direction=received|sent",
        actor="QA_BOT(receiver listing)|QA_BOT(initiator listing)",
        evaluator_key="pre_decision_listings",
        expectation="each decision branch (accept/reject/cancel) is driven against a real pending row visible in the directional request listing BEFORE the decision POST (receiver-side received rows; initiator-side sent row for cancel)",
        assertion_line=181, assertion_quote='local accept_id="$(_parse_pending_id)"',
        request_lines=(177, 180, 210, 211, 231, 232),
        request_quotes=(
            '_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_PM_UUID',
            '_api_authed "GET" "/collaboration/friend-connections/requests?direction=received"',
            '_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_PM_UUID',
            '_api_authed "GET" "/collaboration/friend-connections/requests?direction=received"',
            '_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_PM_UUID',
            '_api_authed "GET" "/collaboration/friend-connections/requests?direction=sent"',
        ),
        seam_module=_SEAM_MUTATIONS, seam_method="run_accept_reject_cancel",
        seam_literals=("the created QA->PM request is not a pending receiver-side row",
                       "the QA->CEO create is not pending before cancel"),
        recorded_keys=("branches",),
        upstream_expectation=("the extraction anchor carries no direct pass credit in the shell; decision POSTs "
                              "185/215/249 stay in the frozen journey seam; fake-id probes 204/225/259 disclosed not-charged"),
        note=("sites r177,r180,r210,r211,r231,r232 with extraction anchor a181; preprod dual receipts retain "
              "decided branch rows only, so the pre-decision listing phase is NOT_OBSERVABLE (dual receipt "
              "projection granularity gap)"),
    ),
)

_MUTUAL_AUTO_APPROVE_LEAVES = (
    _leaf(
        obligation_id="EDGE-MUTUAL-AUTO-APPROVE.pair-pending-creates",
        case_key="mutual-auto-approve", relation=RELATION_REFINES,
        family="EDGE-MUTUAL-AUTO-APPROVE.pair-pending-creates",
        kind="http", method="POST", path="/collaboration/friend-connections/requests",
        actor="CEO_BOT(bearer)|ENG_BOT(bearer)", evaluator_key="pair",
        expectation="the forward CEO->ENG and reverse ENG->CEO creates both land pending with distinct request ids",
        assertion_line=421, assertion_quote="mutual: CEO→ENG create status=$HTTP_STATUS",
        request_lines=(420, 422),
        request_quotes=(
            '_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_ENG_UUID',
            '_api_authed "POST" "/collaboration/friend-connections/requests" "{\\"to_bot\\":\\"$BOT_CEO_UUID',
        ),
        seam_module=_SEAM_MUTATIONS, seam_method="run_mutual_auto_approve",
        seam_literals=("the mutual pair did not both go pending",
                       "the mutual pair collapsed into one request row"),
        recorded_keys=("pair",),
        upstream_expectation="upstream warns both create statuses",
        note="site a421/r420,422; preprod receipts journal the accepted id and edges but not the per-create pending phase -> NOT_OBSERVABLE"),
    _leaf(
        obligation_id="EDGE-MUTUAL-AUTO-APPROVE.accept-decided-received-id",
        case_key="mutual-auto-approve", relation=RELATION_REFINES,
        family="EDGE-MUTUAL-AUTO-APPROVE.accept-decided-received-id",
        kind="http", method="POST", path="/collaboration/friend-connections/requests/$rid/accept",
        actor="ENG_BOT(bearer, receiver)", evaluator_key="accept",
        expectation="ENG accepts the CEO->ENG request parsed from the received pending listing; the accept decides the exact received request id and auto-approves the reverse row",
        assertion_line=432,
        assertion_quote="mutual: accept CEO→ENG status=$HTTP_STATUS (reverse auto-approved, 2 edges built)",
        request_lines=(428, 431),
        request_quotes=(
            '_api_authed "GET" "/collaboration/friend-connections/requests?direction=received"',
            '_api_authed "POST" "/collaboration/friend-connections/requests/$rid/accept"',
        ),
        seam_module=_SEAM_MUTATIONS, seam_method="run_mutual_auto_approve",
        seam_literals=("the forward mutual request is not a pending receiver-side row",
                       "the reverse pending request was not auto-approved", "exact_status=200"),
        recorded_keys=("accepted_request_id", "accepted_edge_admission"),
        upstream_expectation="upstream warns the accept status with the reverse-auto-approve note",
        note=("site a432/r428,431; the accept seam chains the exact-200 wire contract through _accept; "
              "preprod journals the accepted id and edge admission, not the pre-decision parse -> MATCH_PARTIAL")),
    _leaf(
        obligation_id="EDGE-MUTUAL-AUTO-APPROVE.both-edges-built",
        case_key="mutual-auto-approve", relation=RELATION_REFINES,
        family="EDGE-MUTUAL-AUTO-APPROVE.both-edges-built",
        kind="http", method="GET", path="/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot",
        actor="CEO_BOT(listing)|ENG_BOT(listing)", evaluator_key="edges",
        expectation="the single accept builds BOTH edges; both directions' friend-connection listings carry the peer",
        assertion_line=432,
        assertion_quote="mutual: accept CEO→ENG status=$HTTP_STATUS (reverse auto-approved, 2 edges built)",
        request_lines=(431, 438, 440),
        request_quotes=(
            '_api_authed "POST" "/collaboration/friend-connections/requests/$rid/accept"',
            '_api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot"',
            '_api_authed "GET" "/collaboration/friend-connections?actor=$BOT_ENG_UUID&actor_kind=bot"',
        ),
        seam_module=_SEAM_MUTATIONS, seam_method="run_mutual_auto_approve",
        seam_literals=("the mutual accept did not build both edges",
                       "the mutual pair is not listed in both directions"),
        recorded_keys=("edge_id", "mutual"),
        note=("site a432/r431,438,440; preprod journals one accepted edge_id plus mutual=true; the both-edges "
              "count and both-direction listings are not projected -> MATCH_PARTIAL")),
    _leaf(
        obligation_id="EDGE-MUTUAL-AUTO-APPROVE.friend-list-ceo-peer",
        case_key="mutual-auto-approve", relation=RELATION_REFINES,
        family="EDGE-MUTUAL-AUTO-APPROVE.friend-list-ceo-peer",
        kind="http", method="GET", path="/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot",
        actor="CEO_BOT(query-param listing)", evaluator_key="friend_lists.ceo",
        expectation="the CEO friend-connection listing carries the ENG peer after the mutual accept (edge_grants data present)",
        assertion_line=439, assertion_quote="mutual: list CEO friends status=$HTTP_STATUS",
        request_lines=(438,),
        request_quotes=('_api_authed "GET" "/collaboration/friend-connections?actor=$BOT_CEO_UUID&actor_kind=bot"',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_mutual_auto_approve",
        seam_literals=("the mutual pair is not listed in both directions",),
        recorded_keys=("friend_lists",),
        note="site a439/r438; preprod dual receipts journal no per-direction friend listing rows -> NOT_OBSERVABLE"),
    _leaf(
        obligation_id="EDGE-MUTUAL-AUTO-APPROVE.friend-list-eng-peer",
        case_key="mutual-auto-approve", relation=RELATION_REFINES,
        family="EDGE-MUTUAL-AUTO-APPROVE.friend-list-eng-peer",
        kind="http", method="GET", path="/collaboration/friend-connections?actor=$BOT_ENG_UUID&actor_kind=bot",
        actor="ENG_BOT(query-param listing)", evaluator_key="friend_lists.eng",
        expectation="the ENG friend-connection listing carries the CEO peer after the mutual accept (edge_grants data present)",
        assertion_line=441, assertion_quote="mutual: list ENG friends status=$HTTP_STATUS",
        request_lines=(440,),
        request_quotes=('_api_authed "GET" "/collaboration/friend-connections?actor=$BOT_ENG_UUID&actor_kind=bot"',),
        seam_module=_SEAM_MUTATIONS, seam_method="run_mutual_auto_approve",
        seam_literals=("the mutual pair is not listed in both directions",),
        recorded_keys=("friend_lists",),
        note="site a441/r440; preprod dual receipts journal no per-direction friend listing rows -> NOT_OBSERVABLE"),
    _leaf(
        obligation_id="EDGE-MUTUAL-AUTO-APPROVE.admissions-both-authorized",
        case_key="mutual-auto-approve", relation=RELATION_REFINES,
        family="EDGE-MUTUAL-AUTO-APPROVE.admissions-both-authorized",
        kind="http", method="GET", path="/bots/$BOT_ENG_UUID/admission?actor=$BOT_CEO_UUID",
        actor="UNAUTHENTICATED(service-admission, both directions)", evaluator_key="admissions",
        expectation="both directional admissions over the mutual edges are allowed with reason ok sourced from edge_grants",
        assertion_line=445, assertion_quote="mutual: admission CEO→ENG status=$HTTP_STATUS",
        request_lines=(444, 446),
        request_quotes=(
            '_api_authed "GET" "/bots/$BOT_ENG_UUID/admission?actor=$BOT_CEO_UUID"',
            '_api_authed "GET" "/bots/$BOT_CEO_UUID/admission?actor=$BOT_ENG_UUID"',
        ),
        seam_module=_SEAM_MUTATIONS, seam_method="run_mutual_auto_approve",
        seam_literals=("the CEO->QA mutual admission was not authorized",
                       "the QA->CEO mutual admission was not authorized"),
        recorded_keys=("admissions",),
        upstream_expectation=("upstream warns both admission statuses; the owned-local seam runs the mutual pair as "
                              "CEO<->QA (documented correction: the historical CEO<->ENG pair's revoked edge rows cannot "
                              "rebuild under the unique-key insert no-op); the obligation is the auto-approve mechanism itself"),
        note="site a445,a447/r444,446"),
    _leaf(
        obligation_id="EDGE-MUTUAL-AUTO-APPROVE.all-direction-decided-rows",
        case_key="mutual-auto-approve", relation=RELATION_REFINES,
        family="EDGE-MUTUAL-AUTO-APPROVE.all-direction-decided-rows",
        kind="http", method="GET", path="/collaboration/friend-connections/requests?direction=all",
        actor="CEO_BOT(bearer)|ENG_BOT(bearer)", evaluator_key="all_direction_listings",
        expectation="the direction=all request listings for both bots union sent and received decided rows with decided_at parsed back, non-empty",
        assertion_line=450, assertion_quote="unions with decided rows, parsing decided_at back from the DB",
        request_lines=(451, 452),
        request_quotes=('_api_authed "GET" "/collaboration/friend-connections/requests?direction=all"', '_api_authed "GET" "/collaboration/friend-connections/requests?direction=all"'),
        seam_module=_SEAM_MUTATIONS, seam_method="run_mutual_auto_approve",
        seam_literals=("the all-direction mutual listing is empty",),
        recorded_keys=("all_direction_listings",),
        upstream_expectation=("upstream sends the two all-direction probes with stdout discarded and no pass credit; the anchor "
                              "is the line-450 comment documenting the sent+all union with decided rows; the owned-local seam pins a non-empty union"),
        note="site a450(comment anchor)/r451,452 silent probes; preprod receipts journal no all-direction rows -> NOT_OBSERVABLE"),
)

ALL_LEAVES = (_ERROR_ADMISSION_LEAVES + _CONNECT_DISCOVER_LEAVES
              + _FRIEND_REQUEST_AND_LIST_LEAVES + _ACCEPT_REJECT_CANCEL_LEAVES
              + _MUTUAL_AUTO_APPROVE_LEAVES)

LEAVES_BY_CASE_KEY: dict[str, tuple[dict[str, Any], ...]] = {}
for _row in ALL_LEAVES:
    LEAVES_BY_CASE_KEY.setdefault(_row["case_key"], []).append(_row)
LEAVES_BY_CASE_KEY = {key: tuple(rows) for key, rows in LEAVES_BY_CASE_KEY.items()}


@dataclass(frozen=True)
class DensityLeaf:
    """One source-anchored density obligation row."""

    obligation_id: str
    case_key: str
    parent_id: str
    relation: str
    family: str
    kind: str
    method: str
    path: str
    actor: str
    evaluator_key: str
    expectation: str
    assertion_line: int
    assertion_quote: str
    request_lines: tuple[int, ...]
    request_quotes: tuple[str, ...]
    seam: dict[str, Any]
    recorded_keys: tuple[str, ...]
    upstream_expectation: str | None
    note: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id, "case_key": self.case_key,
            "parent_id": self.parent_id, "relation": self.relation, "family": self.family,
            "kind": self.kind, "method": self.method, "path": self.path, "actor": self.actor,
            "evaluator_key": self.evaluator_key, "expectation": self.expectation,
            "source_file": SOURCE_RELPATH, "assertion_line": self.assertion_line,
            "assertion_quote": self.assertion_quote,
            "request_lines": list(self.request_lines), "request_quotes": list(self.request_quotes),
            "seam": dict(self.seam), "recorded_keys": list(self.recorded_keys),
            "upstream_expectation": self.upstream_expectation, "note": self.note,
        }


def _source_lines() -> list[str]:
    if SOURCE_PATH.is_symlink() or not SOURCE_PATH.is_file():
        raise RunStateError("edge_permission.sh source is missing or symlinked")
    if SOURCE_PATH.stat().st_size > 1_048_576:
        raise RunStateError("edge_permission.sh source is oversized")
    return SOURCE_PATH.read_text(encoding="utf-8").splitlines()


def source_digest() -> str:
    return hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest()


def _function_body_lines(lines: list[str], parent: str) -> list[int]:
    """Return 1-based line numbers of one parent function body, brace-scoped."""
    marker = parent + "() {"
    starts = [index for index, line in enumerate(lines) if line.startswith(marker)]
    if len(starts) != 1:
        raise RunStateError("edge_permission.sh drift: " + parent + " is missing or duplicated")
    body: list[int] = []
    for index in range(starts[0], len(lines)):
        body.append(index + 1)
        if lines[index] == "}":
            return body
    raise RunStateError("edge_permission.sh drift: " + parent + " is not brace-terminated")


def _anchor(lines: list[str], line_no: int, quote: str, tag: str) -> None:
    if line_no < 1 or line_no > len(lines) or quote not in lines[line_no - 1]:
        raise RunStateError("edge_permission.sh anchor drift: " + tag
                            + " (edge_permission.sh:" + str(line_no) + ")")


def scanned_sites(parent: str) -> list[dict[str, Any]]:
    """Every transport probe and fixture-write call line inside one parent body."""
    lines = _source_lines()
    body = _function_body_lines(lines, parent)
    rows = []
    for number in body:
        text = lines[number - 1]
        if _TRANSPORT_RE.match(text) or _FIXTURE_WRITE_RE.match(text):
            rows.append({"line": number, "text": text.strip()})
    return rows


def charged_site_lines(parent: str) -> set[int]:
    lines: set[int] = set()
    for row in LEAVES_BY_CASE_KEY.get(CASE_KEY_BY_PARENT[parent], ()):
        lines.update(row["request_lines"])
    return lines


def not_charged_site_lines(parent: str) -> set[int]:
    lines: set[int] = set()
    for entry in NOT_CHARGED_SOURCE_SITES:
        if entry["parent"] == parent:
            lines.update(entry["lines"])
    return lines


def density_site_classification(parent: str) -> dict[str, Any]:
    """Classify every scanned site of one parent; fail closed on gaps."""
    scanned = {row["line"] for row in scanned_sites(parent)}
    charged = charged_site_lines(parent) & scanned
    frozen = ({site["line"] for site in FROZEN_READ_SLICE_SITES
               if site["parent"] == parent}) & scanned
    disclosed = not_charged_site_lines(parent) & scanned
    if charged & frozen or charged & disclosed or frozen & disclosed:
        raise RunStateError("edge_permission density site double-charged: " + parent)
    unclassified = scanned - charged - frozen - disclosed
    if unclassified:
        raise RunStateError("edge_permission density completeness guard failed: " + parent
                            + " unclassified sites "
                            + ",".join(str(line) for line in sorted(unclassified)))
    non_site_disclosures = not_charged_site_lines(parent) - scanned
    for number in not_charged_site_lines(parent) & scanned:
        pass
    return {
        "parent": parent, "scanned_sites": sorted(scanned),
        "charged_sites": sorted(charged), "frozen_read_slice_sites": sorted(frozen),
        "not_charged_sites": sorted(disclosed),
        "non_site_disclosures": sorted(non_site_disclosures), "unclassified": [],
    }


def _density_leaf(row: dict[str, Any]) -> DensityLeaf:
    return DensityLeaf(
        obligation_id=row["obligation_id"], case_key=row["case_key"],
        parent_id=row["parent_id"], relation=row["relation"], family=row["family"],
        kind=row["kind"], method=row["method"], path=row["path"], actor=row["actor"],
        evaluator_key=row["evaluator_key"], expectation=row["expectation"],
        assertion_line=row["assertion_line"], assertion_quote=row["assertion_quote"],
        request_lines=tuple(row["request_lines"]), request_quotes=tuple(row["request_quotes"]),
        seam=dict(row["seam"]), recorded_keys=tuple(row["recorded_keys"]),
        upstream_expectation=row["upstream_expectation"], note=row["note"])


def build_density_obligations() -> tuple[DensityLeaf, ...]:
    """Build and anchor-validate the 22-leaf density roster, fail-closed."""
    lines = _source_lines()
    bodies = {parent: _function_body_lines(lines, parent) for parent in PARENTS}
    for parent in PARENTS:
        body_text = "\n".join(lines[number - 1] for number in bodies[parent])
        for literal in REQUIRED_BODY_LITERALS[parent]:
            if literal not in body_text:
                raise RunStateError("edge_permission.sh body drift: " + parent
                                    + " no longer contains " + literal)
    for row in ALL_LEAVES:
        parent = row["parent_id"]
        if parent not in bodies:
            raise RunStateError("density leaf references an unmanaged parent: " + parent)
        _anchor(lines, row["assertion_line"], row["assertion_quote"], row["obligation_id"])
        if len(row["request_lines"]) != len(row["request_quotes"]):
            raise RunStateError("density leaf request anchor mismatch: " + row["obligation_id"])
        for number, quote in zip(row["request_lines"], row["request_quotes"]):
            _anchor(lines, number, quote, row["obligation_id"])
        if row["relation"] not in RELATIONS:
            raise RunStateError("density leaf relation invalid: " + row["obligation_id"])
        for number in row["request_lines"]:
            if number not in bodies[parent]:
                raise RunStateError("density leaf request anchor outside its parent body: "
                                    + row["obligation_id"] + " line " + str(number))
        if row["assertion_line"] not in bodies[parent]:
            raise RunStateError("density leaf assertion anchor outside its parent body: "
                                + row["obligation_id"])
        if row["case_key"] != CASE_KEY_BY_PARENT[parent]:
            raise RunStateError("density leaf case key drifted: " + row["obligation_id"])
    ids = [row["obligation_id"] for row in ALL_LEAVES]
    if len(ids) != len(set(ids)):
        raise RunStateError("density obligation ids are not unique")
    if len(ids) != DENSITY_LEAF_COUNT:
        raise RunStateError("density leaf count changed: expected " + str(DENSITY_LEAF_COUNT))
    for entry in NOT_CHARGED_SOURCE_SITES:
        parent = entry["parent"]
        if parent not in bodies:
            raise RunStateError("not-charged disclosure references an unmanaged parent")
        for number in entry["lines"]:
            if number not in bodies[parent]:
                raise RunStateError("not-charged disclosure line outside its parent body: "
                                    + parent + " line " + str(number))
    for site in FROZEN_READ_SLICE_SITES:
        _anchor(lines, site["line"], site["quote"], site["requirement_id"])
    for parent in PARENTS:
        density_site_classification(parent)
    for parent in PARENTS:
        if EXISTING_BY_PARENT[parent] + ADDED_BY_PARENT[parent] != PARSED_BY_PARENT[parent]:
            raise RunStateError("density reconciliation failed for " + parent)
    if EXISTING_TOTAL + DENSITY_LEAF_COUNT != PARSED_TOTAL:
        raise RunStateError("density roster reconciliation failed: 6 + 22 != 28")
    return tuple(_density_leaf(row) for row in ALL_LEAVES)


def density_reconciliation() -> dict[str, Any]:
    """Disclose the roster accounting honestly, per parent and in total."""
    per_parent = {}
    for parent in PARENTS:
        classification = density_site_classification(parent)
        per_parent[parent] = {
            "case_key": CASE_KEY_BY_PARENT[parent],
            "parsed": PARSED_BY_PARENT[parent],
            "frozen_existing_obligations": EXISTING_BY_PARENT[parent],
            "density_leaves": ADDED_BY_PARENT[parent],
            "projected_superset": PROJECTED_SUPERSET_BY_PARENT[parent],
            "charged_sites": classification["charged_sites"],
            "frozen_read_slice_sites": classification["frozen_read_slice_sites"],
            "not_charged_sites": classification["not_charged_sites"],
            "non_site_disclosures": classification["non_site_disclosures"],
        }
    return {
        "roster_snapshot": ("assertion-gap-matrix-20261031.md section A2 rows #32-#35/#39; "
                            "coverage-map-20261031.md section 2 rows #32-#35/#39"),
        "parsed_total": PARSED_TOTAL, "frozen_existing_total": EXISTING_TOTAL,
        "density_leaves_total": DENSITY_LEAF_COUNT,
        "projected_superset_total": EXISTING_TOTAL + DENSITY_LEAF_COUNT,
        "per_parent": per_parent,
        "not_charged_source_sites": [
            {"parent": entry["parent"], "lines": list(entry["lines"]),
             "classification": entry["classification"], "reason": entry["reason"]}
            for entry in NOT_CHARGED_SOURCE_SITES],
        "frozen_read_slice_sites": [dict(site) for site in FROZEN_READ_SLICE_SITES],
        "upstream_native_assertion_ids": False,
        "decomposition_rule": (
            "parsed denominators are taken verbatim from the 2026-10-31 roster snapshot; each parent "
            "decomposes as frozen executed obligations + anchored density leaves == parsed; upstream "
            "pass/warn accounting the roster did not count (conditional decision POSTs, warn-only probes, "
            "fake-id degrade probes, journey completion passes) is classified as frozen journey seam or "
            "disclosed not-charged, never silently charged"),
        "honesty": (
            "no obligation exists without an edge_permission.sh line anchor and upstream quote; fixture "
            "writes, warm-ups, degrade probes, warn-only journey probes and completion passes are "
            "disclosed, never counted; a request line may feed two leaves by design (the mutual accept "
            "POST anchors both the decided-row leaf and the both-edges leaf)"),
        "source_digest": source_digest(),
    }
