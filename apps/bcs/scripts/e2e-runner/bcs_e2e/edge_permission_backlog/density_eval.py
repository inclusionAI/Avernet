"""Density leaf evaluator and recorded-receipt classifier.

Two postures share one predicate table so the overlay can never quietly
disagree with itself:

* :func:`evaluate_leaf` is the future ENROLLED execution posture.  An
  observation is a per-leaf recorded-keys projection of one executed
  response set; missing observations are ``NOT_OBSERVABLE`` (silent, never
  charged), a true predicate is ``PASS``, and a false predicate is ``FAIL``.
  Under this strict posture the idempotent-duplicate leaf requires the same
  request id to be echoed, so the historical dual-receipt divergence fails
  here by design.
* :func:`classify_recorded` is the analytical re-read posture used by the
  preprod arm today.  It re-reads the already-recorded r5/r6/r7 dual
  receipts and reports ``MATCH`` / ``MATCH_PARTIAL`` / ``DRIFT_DISCLOSED``
  / ``NOT_OBSERVABLE`` with drift and material-gap disclosures.  A partial
  or drift classification never becomes PASS credit and never flips the
  frozen family verdicts.

Every rule keys on the leaf ``evaluator_key`` and only touches the recorded
projection keys declared in the roster; nothing here opens a transport or
resolves a credential.
"""
from __future__ import annotations

from typing import Any

from ..run_state import RunStateError

STATUS_MATCH = "MATCH"
STATUS_MATCH_PARTIAL = "MATCH_PARTIAL"
STATUS_DRIFT_DISCLOSED = "DRIFT_DISCLOSED"
STATUS_NOT_OBSERVABLE = "NOT_OBSERVABLE"
CLASSIFICATION_STATUSES = (
    STATUS_MATCH, STATUS_MATCH_PARTIAL, STATUS_DRIFT_DISCLOSED,
    STATUS_NOT_OBSERVABLE)

EVALUATION_PASS = "PASS"
EVALUATION_FAIL = "FAIL"
EVALUATION_NOT_OBSERVABLE = "NOT_OBSERVABLE"

OBSERVATION_MODEL: dict[str, dict[str, Any]] = {
    "admission_branches.unknown_bot": {
        "required": ("admission_branches",),
        "rule": "unknown-bot admission refused with reason_code bot_not_found",
    },
    "admission_branches.protected_no_edge": {
        "required": ("admission_branches",),
        "rule": "protected no-edge admission refused with reason_code no_edge",
    },
    "admission_branches.protected_no_edge_human": {
        "required": ("admission_branches",),
        "rule": "protected no-edge admission for the actor_kind=human form refused no_edge",
    },
    "self_add": {
        "required": ("self_add",),
        "rule": "self-add create refused 400 with error_code cannot_add_self",
    },
    "unknown_bot_create": {
        "required": ("unknown_bot",),
        "rule": "create with unknown to_bot refused 404 with error_code bot_not_found",
    },
    "duplicate": {
        "required": ("duplicate_pending_count", "duplicate_request_ids"),
        "rule": "one idempotent pending row echoing the same request_ids",
    },
    "visibility_writes.private": {
        "required": ("visibility_writes",),
        "rule": "owner private visibility write journaled (dual receipts carry none)",
    },
    "private_create": {
        "required": ("private_create_defect",),
        "rule": "private-target create refused 403 on the canonical contract",
    },
    "private_discovery": {
        "required": ("private_discovery_empty",),
        "rule": "private collaborate_bot discovery count 0 and empty bots",
    },
    "visibility_writes.public": {
        "required": ("visibility_writes",),
        "rule": "owner public visibility restore journaled (dual receipts carry none)",
    },
    "public_discovery": {
        "required": ("public_discovery_count", "public_discovery_self_excluded",
                     "public_directory_driver_listed"),
        "rule": "public discovery count agrees with entries, requester self-excluded, directory driver listed",
    },
    "role_branch": {
        "required": ("role_no_org_drift",),
        "rule": "role without organization_code refused 400 carrying role_requires_organization_code",
    },
    "organization_branch": {
        "required": ("organization_no_requester_drift",),
        "rule": "organization discovery without a requester bot fails closed with an error body",
    },
    "create_request_annex": {
        "required": ("cited",),
        "rule": "CEO->ENG create answered 200/201 with non-empty request_ids (cross-family citation)",
    },
    "pre_decision_listings": {
        "required": ("pre_decision_listings",),
        "rule": "directional pending listing shows the row BEFORE the decision POST",
    },
    "pair": {
        "required": ("pair",),
        "rule": "forward and reverse creates both land pending with distinct request ids",
    },
    "accept": {
        "required": ("accepted_request_id",),
        "rule": "accept decides the exact received pending request id",
    },
    "edges": {
        "required": ("edge_id", "mutual"),
        "rule": "one accept builds both edges and both directions list the peer",
    },
    "friend_lists.ceo": {
        "required": ("friend_lists",),
        "rule": "CEO friend listing carries the ENG peer after the mutual accept",
    },
    "friend_lists.eng": {
        "required": ("friend_lists",),
        "rule": "ENG friend listing carries the CEO peer after the mutual accept",
    },
    "admissions": {
        "required": ("admissions",),
        "rule": "both directional admissions allowed with reason ok sourced from edge_grants",
    },
    "all_direction_listings": {
        "required": ("all_direction_listings",),
        "rule": "direction=all listings union sent and received decided rows, non-empty",
    },
}


def _dig(recorded: dict[str, Any], path: str) -> Any:
    value: Any = recorded
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _missing_keys(recorded: dict[str, Any], evaluator_key: str) -> list[str]:
    model = OBSERVATION_MODEL.get(evaluator_key, {})
    required = model.get("required", ())
    if not required:
        return []
    if not isinstance(recorded, dict):
        return list(required)
    return [key for key in required
            if _dig(recorded, key) is None]


def _admission_rule(recorded: dict[str, Any], branch: str,
                    expected: str) -> tuple[str, tuple[str, ...], str]:
    branches = _dig(recorded, "admission_branches")
    if not isinstance(branches, dict) or branch not in branches:
        return STATUS_NOT_OBSERVABLE, (), "admission branches are not journaled in the dual receipt projection"
    if branches[branch] == expected:
        return STATUS_MATCH, (), "observed reason_code " + str(branches[branch])
    return STATUS_DRIFT_DISCLOSED, ("ADMISSION-REASON-DRIFT",), (
        "observed " + str(branches[branch]) + " instead of " + expected)


def _duplicate_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    count = _dig(recorded, "duplicate_pending_count")
    ids = _dig(recorded, "duplicate_request_ids")
    if count is None or ids is None:
        return STATUS_NOT_OBSERVABLE, (), "duplicate branch rows are not journaled in the dual receipt projection"
    if count != 1:
        return STATUS_DRIFT_DISCLOSED, ("DUP-CREATE-ROW-COUNT",), (
            "pending row count " + str(count) + " instead of the idempotent 1")
    distinct = {item for item in ids} if isinstance(ids, list) else set()
    if len(distinct) == 1:
        return STATUS_MATCH, (), "one pending row echoing the same request id"
    return STATUS_MATCH_PARTIAL, ("DUP-CREATE-ID-ECHO",), (
        "row idempotence holds (one pending row) but the receipt echoed "
        + str(len(distinct)) + " distinct request ids")


def _self_add_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    self_add = _dig(recorded, "self_add")
    if not isinstance(self_add, dict):
        return STATUS_NOT_OBSERVABLE, (), (
            "self-add branch rows are not journaled in the dual receipt projection")
    status = self_add.get("http_status")
    payload = self_add.get("data")
    code = payload.get("error_code") if isinstance(payload, dict) else None
    if status == 400 and code == "cannot_add_self":
        return STATUS_MATCH, (), "self-add create refused 400 with error_code cannot_add_self"
    return STATUS_DRIFT_DISCLOSED, ("SELF-ADD-DRIFT",), (
        "self-add branch answered " + str(status) + " with error_code " + str(code))


def _unknown_bot_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    unknown = _dig(recorded, "unknown_bot")
    if not isinstance(unknown, dict):
        return STATUS_NOT_OBSERVABLE, (), (
            "unknown-target create rows are not journaled in the dual receipt projection")
    status = unknown.get("http_status")
    payload = unknown.get("data")
    code = payload.get("error_code") if isinstance(payload, dict) else None
    if status == 404 and code == "bot_not_found":
        return STATUS_MATCH, (), "unknown-target create refused 404 with error_code bot_not_found"
    return STATUS_DRIFT_DISCLOSED, ("UNKNOWN-BOT-DRIFT",), (
        "unknown-target create answered " + str(status) + " with error_code " + str(code))


def _visibility_rule(recorded: dict[str, Any], side: str) -> tuple[str, tuple[str, ...], str]:
    writes = _dig(recorded, "visibility_writes")
    if not isinstance(writes, dict) or side not in writes:
        return STATUS_NOT_OBSERVABLE, (), (
            "visibility writes are not journaled in the dual receipt projection"
            " (material gap; the leaf rides the fixture seam)")
    if writes[side]:
        return STATUS_MATCH, (), "owner visibility write journaled"
    return STATUS_DRIFT_DISCLOSED, ("VISIBILITY-WRITE-DRIFT",), "visibility write journaled as falsy"


def _private_create_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    defect = _dig(recorded, "private_create_defect")
    if not isinstance(defect, dict):
        return STATUS_NOT_OBSERVABLE, (), "private-target create is not journaled in the dual receipt projection"
    status = defect.get("http_status")
    if status == 403:
        return STATUS_MATCH, (), "private-target create refused 403"
    if status == 500:
        return STATUS_MATCH_PARTIAL, ("PD-PRIVATE-CREATE-500",), (
            "canonical contract refuses 403 (PrivateBotCannotCollaborate); the"
            " recorded preprod leg answered 500 code 50000"
            " data.error_code=internal_error; product defect PD-PRIVATE-CREATE-500 on file")
    return STATUS_DRIFT_DISCLOSED, ("PD-PRIVATE-CREATE-STATUS",), (
        "private-target create answered " + str(status))


def _public_discovery_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    count = _dig(recorded, "public_discovery_count")
    self_excluded = _dig(recorded, "public_discovery_self_excluded")
    driver_listed = _dig(recorded, "public_directory_driver_listed")
    if count is None or self_excluded is None or driver_listed is None:
        return STATUS_NOT_OBSERVABLE, (), "public discovery rows are not journaled in the dual receipt projection"
    if not (isinstance(count, int) and self_excluded is True and driver_listed is True):
        return STATUS_DRIFT_DISCLOSED, ("PD-DISCOVER-PUBLIC-SHAPE",), (
            "public discovery projection contradicts the canonical shape")
    entries = _dig(recorded, "public_discovery_entries")
    if isinstance(entries, list) and entries and all(
            isinstance(item, dict) and "bot_uuid" in item
            and "visibility" in item and "is_friend" in item
            for item in entries):
        return STATUS_MATCH, (), "count agreement, self-exclusion, directory listing and per-entry shape all journaled"
    return STATUS_MATCH_PARTIAL, (), (
        "count agreement (" + str(count) + "), requester self-exclusion and"
        " directory-driver listing are journaled; the per-entry"
        " bot_uuid/visibility/is_friend shape is not projected in the dual receipt")


def _role_branch_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    drift = _dig(recorded, "role_no_org_drift")
    if not isinstance(drift, dict):
        return STATUS_NOT_OBSERVABLE, (), "role branch rows are not journaled in the dual receipt projection"
    message = str(drift.get("message") or "")
    if drift.get("http_status") == 400 and "role_requires_organization_code" in message:
        return STATUS_MATCH, (), (
            "canonical code role_requires_organization_code preserved"
            " (wrapper invalid_operation disclosed as PD-DISCOVER-BRANCH-DRIFT context)")
    return STATUS_DRIFT_DISCLOSED, ("PD-DISCOVER-BRANCH-DRIFT",), (
        "role branch answered " + str(drift.get("http_status")))


def _organization_branch_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    drift = _dig(recorded, "organization_no_requester_drift")
    if not isinstance(drift, dict):
        return STATUS_NOT_OBSERVABLE, (), "organization branch rows are not journaled in the dual receipt projection"
    status = drift.get("http_status")
    if status == 400:
        return STATUS_MATCH, (), "organization discovery without a requester fails closed 400"
    if status in (401, 403, 404) and drift.get("message"):
        return STATUS_MATCH_PARTIAL, ("PD-DISCOVER-BRANCH-DRIFT",), (
            "fail-closed posture preserved (error body journaled) with wire"
            " code drift " + str(status) + " vs canonical 400")
    return STATUS_DRIFT_DISCLOSED, ("PD-DISCOVER-BRANCH-DRIFT",), (
        "organization branch answered " + str(status))


def _annex_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(recorded, dict) or recorded.get("cited") is not True:
        return STATUS_NOT_OBSERVABLE, (), (
            "the WRITE_ANNEX leaf has no enrolled receipt of its own")
    total = recorded.get("create_rows_total")
    answered = recorded.get("create_rows_201")
    if not isinstance(total, int) or not isinstance(answered, int):
        return STATUS_NOT_OBSERVABLE, (), "cited cross-family create rows are not countable"
    if answered >= 1:
        return STATUS_MATCH_PARTIAL, (), (
            "cited r5 mutation-round receipt journals "
            + str(answered) + " friend_connection_mutation.create row(s) answered 201"
            " with request ids echoed; adjacent citation only, no completion"
            " credit to the frozen read slice")
    if total == 0:
        return STATUS_DRIFT_DISCLOSED, ("PD-CREATE-ANNEX-NO-ROWS",), (
            "cited receipt journals no friend_connection_mutation.create rows")
    return STATUS_DRIFT_DISCLOSED, ("PD-CREATE-ANNEX-NO-201",), (
        "cited receipt journals create rows but none answered 201")


def _strict_pair_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    if not isinstance(recorded, dict) or "pair" not in recorded:
        return STATUS_NOT_OBSERVABLE, (), "per-create pending rows are not journaled in the dual receipt projection"
    pair = recorded["pair"]
    if isinstance(pair, dict) and len(pair.get("request_ids", [])) == 2:
        return STATUS_MATCH, (), "both directional creates journaled pending with distinct ids"
    return STATUS_DRIFT_DISCLOSED, ("PAIR-DRIFT",), "mutual pair projection drifted"


def _listing_rule(recorded: dict[str, Any], key: str,
                  label: str) -> tuple[str, tuple[str, ...], str]:
    listings = _dig(recorded, key)
    if not isinstance(listings, dict) or not listings.get("non_empty"):
        return STATUS_NOT_OBSERVABLE, (), (
            label + " rows are not journaled in the dual receipt projection")
    return STATUS_MATCH, (), label + " journaled non-empty"


def _friend_list_rule(recorded: dict[str, Any], side: str) -> tuple[str, tuple[str, ...], str]:
    listings = _dig(recorded, "friend_lists")
    if not isinstance(listings, dict) or side not in listings:
        return STATUS_NOT_OBSERVABLE, (), (
            "per-direction friend listings are not journaled in the dual receipt projection")
    if listings[side]:
        return STATUS_MATCH, (), side + " friend listing carries the peer"
    return STATUS_DRIFT_DISCLOSED, ("MUTUAL-FRIEND-LIST-DRIFT",), side + " listing misses the peer"


def _admissions_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    admissions = _dig(recorded, "admissions")
    if not isinstance(admissions, dict) or not admissions:
        return STATUS_NOT_OBSERVABLE, (), "directional admissions are not journaled in the dual receipt projection"
    rows = list(admissions.values())
    if not all(isinstance(row, dict) and row.get("allowed") is True
               and row.get("reason") == "ok" and row.get("source") == "edge_grant"
               for row in rows):
        return STATUS_DRIFT_DISCLOSED, ("MUTUAL-ADMISSION-DRIFT",), (
            "a directional admission is not allowed/ok/edge_grant")
    if len(rows) == 2:
        return STATUS_MATCH, (), "both directional admissions allowed with reason ok sourced from edge_grants"
    return STATUS_MATCH_PARTIAL, (), (
        str(len(rows)) + " direction(s) journaled; both-direction posture needs the enrolled leg")


def _accepted_rule(recorded: dict[str, Any], key: str,
                   label: str) -> tuple[str, tuple[str, ...], str]:
    value = _dig(recorded, key)
    if value is None:
        return STATUS_NOT_OBSERVABLE, (), label + " is not journaled in the dual receipt projection"
    return STATUS_MATCH_PARTIAL, (), (
        label + " journaled (" + str(value) + "); the pre-decision directional"
        " listing parse is not projected (receipt granularity gap)")


def _edges_rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
    edge_id = _dig(recorded, "edge_id")
    mutual = _dig(recorded, "mutual")
    if edge_id is None or mutual is None:
        return STATUS_NOT_OBSERVABLE, (), "edge/mutual rows are not journaled in the dual receipt projection"
    if mutual is True and edge_id is not None:
        return STATUS_MATCH_PARTIAL, (), (
            "one edge id and mutual=true journaled; the both-edges count and"
            " both-direction listings are not projected (receipt granularity gap)")
    return STATUS_DRIFT_DISCLOSED, ("MUTUAL-EDGE-DRIFT",), "edge/mutual projection drifted"


def _simple_rule(key: str, label: str):
    def rule(recorded: dict[str, Any]) -> tuple[str, tuple[str, ...], str]:
        value = _dig(recorded, key)
        if value is None:
            return STATUS_NOT_OBSERVABLE, (), label + " is not journaled in the dual receipt projection"
        if value is True:
            return STATUS_MATCH, (), label + " journaled true"
        return STATUS_DRIFT_DISCLOSED, (label.upper().replace(" ", "-") + "-DRIFT",), label + " journaled falsy"
    return rule


CLASSIFICATION_RULES = {
    "admission_branches.unknown_bot": lambda recorded: _admission_rule(recorded, "unknown_bot", "bot_not_found"),
    "admission_branches.protected_no_edge": lambda recorded: _admission_rule(recorded, "protected_no_edge", "no_edge"),
    "admission_branches.protected_no_edge_human": lambda recorded: _admission_rule(recorded, "protected_no_edge_human", "no_edge"),
    "self_add": lambda recorded: _self_add_rule(recorded),
    "unknown_bot_create": lambda recorded: _unknown_bot_rule(recorded),
    "duplicate": _duplicate_rule,
    "visibility_writes.private": lambda recorded: _visibility_rule(recorded, "private"),
    "private_create": _private_create_rule,
    "private_discovery": _simple_rule("private_discovery_empty", "private discovery emptiness"),
    "visibility_writes.public": lambda recorded: _visibility_rule(recorded, "public"),
    "public_discovery": _public_discovery_rule,
    "role_branch": _role_branch_rule,
    "organization_branch": _organization_branch_rule,
    "create_request_annex": _annex_rule,
    "pre_decision_listings": lambda recorded: _listing_rule(recorded, "pre_decision_listings", "pre-decision directional listings"),
    "pair": _strict_pair_rule,
    "accept": lambda recorded: _accepted_rule(recorded, "accepted_request_id", "decided accept request id"),
    "edges": _edges_rule,
    "friend_lists.ceo": lambda recorded: _friend_list_rule(recorded, "ceo"),
    "friend_lists.eng": lambda recorded: _friend_list_rule(recorded, "eng"),
    "admissions": _admissions_rule,
    "all_direction_listings": lambda recorded: _listing_rule(recorded, "all_direction_listings", "direction=all decided-row union"),
}


def classify_recorded(leaf: dict[str, Any], recorded: Any = None) -> dict[str, Any]:
    """Classify one leaf against its recorded projection, fail-honest."""
    evaluator_key = leaf["evaluator_key"]
    rule = CLASSIFICATION_RULES.get(evaluator_key)
    if rule is None:
        raise RunStateError(
            "density classifier lacks rule for evaluator " + evaluator_key)
    if not isinstance(recorded, dict):
        status, drifts, basis = STATUS_NOT_OBSERVABLE, (), (
            "no recorded projection attached for evaluator " + evaluator_key)
    else:
        status, drifts, basis = rule(recorded)
    if status not in CLASSIFICATION_STATUSES:
        raise RunStateError(
            "density classifier rule returned an invalid status for evaluator "
            + evaluator_key + ": " + str(status))
    return {
        "obligation_id": leaf["obligation_id"],
        "evaluator_key": evaluator_key,
        "recorded_keys": list(leaf["recorded_keys"]),
        "status": status,
        "drifts": list(drifts),
        "basis": basis,
    }


def _missing_observation(recorded: Any, evaluator_key: str) -> list[str]:
    return _missing_keys(recorded if isinstance(recorded, dict) else {}, evaluator_key)


def _predicate(evaluator_key: str, observation: dict[str, Any]) -> bool | None:
    """Strict enrolled posture: True/False verdict, None when unobservable."""
    rule = CLASSIFICATION_RULES.get(evaluator_key)
    if rule is None:
        return None
    status, _drifts, _basis = rule(observation)
    if status == STATUS_NOT_OBSERVABLE:
        return None
    if status == STATUS_MATCH:
        return True
    if status == STATUS_MATCH_PARTIAL and evaluator_key in (
            "accept", "edges", "admissions", "create_request_annex"):
        return True
    return False


def evaluate_leaf(leaf: dict[str, Any], observation: Any = None) -> dict[str, Any]:
    """Evaluate one leaf against an enrolled observation projection."""
    evaluator_key = leaf["evaluator_key"]
    if not isinstance(observation, dict):
        return {
            "obligation_id": leaf["obligation_id"],
            "evaluator_key": evaluator_key,
            "status": EVALUATION_NOT_OBSERVABLE,
            "missing_keys": list(OBSERVATION_MODEL.get(evaluator_key, {}).get("required", ())),
            "observed": None,
            "basis": "no observation attached for evaluator " + evaluator_key,
        }
    missing = [key for key in _missing_observation(observation, evaluator_key)]
    verdict = _predicate(evaluator_key, observation)
    if verdict is None:
        status = EVALUATION_NOT_OBSERVABLE
    elif verdict is True:
        status = EVALUATION_PASS
    else:
        status = EVALUATION_FAIL
    return {
        "obligation_id": leaf["obligation_id"],
        "evaluator_key": evaluator_key,
        "status": status,
        "missing_keys": missing,
        "observed": observation,
        "basis": OBSERVATION_MODEL.get(evaluator_key, {}).get("rule", ""),
    }


def classification_totals(classified: list[dict[str, Any]]) -> dict[str, int]:
    totals = {status: 0 for status in CLASSIFICATION_STATUSES}
    totals["total"] = 0
    for row in classified:
        status = row.get("status")
        if status in totals:
            totals[status] += 1
        totals["total"] += 1
    return totals
