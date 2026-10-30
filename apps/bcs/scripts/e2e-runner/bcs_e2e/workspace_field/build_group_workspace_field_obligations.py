"""Build the committed field-level contract for the Workspace story.

This builder deliberately writes below the Workspace family package.  It does
not create or consume the historical runner-root migration artifact.  The
63-atom density contract remains the only atom source; this file adds the
17-to-43 obligation roll-up and field metadata around it.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

RUNNER_ROOT = Path(__file__).resolve().parents[2]
E2E_TEST_ROOT = RUNNER_ROOT.parent / "e2e-test"
OUTPUT_PATH = Path(__file__).resolve().parent / "workspace-field-obligations-contract.json"
DENSITY_CONTRACT_PATH = RUNNER_ROOT / "group-workspace-density-contract.json"
MIGRATION_CONTRACTS_PATH = RUNNER_ROOT / "migration-contracts.json"
STORY = "story_user_operates_group_workspace"
CONTRACT = "workspace-field-obligations-v1"
BASELINE_COUNT = 17
FIELD_COUNT = 26
TOTAL_COUNT = 43

# Each tuple is (id, expectation, parent, decomposition_group, atom_ids,
# source_file, source_function, line_start, line_end, source_quote,
# field_name, write_path, read_path, error_envelope, concurrency_isolation).
FIELD_ROWS = [
    ("WORKSPACE.api.members.detail.participant-count", "group has 3 participants", "WORKSPACE.create", "WORKSPACE.create", ["WORKSPACE.api.members.detail.participant-count"], "group.sh", "test_create_group_with_members", 61, 61, 'assert_eq "group has 3 participants" "$count" "3"', "participants[]", "POST /groups.participants", "GET /groups/{group_id}.participants", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.api.add.participant-count", "group has 2 participants after add", "WORKSPACE.members", "WORKSPACE.members", ["WORKSPACE.api.add.participant-count"], "group.sh", "test_add_member", 128, 128, 'assert_eq "group has 2 participants after add" "$count" "2"', "participants[]", "POST /groups/{group_id}/members.bot_uuid", "GET /groups/{group_id}.participants", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.api.remove.participant-count", "group has 1 participant after remove", "WORKSPACE.members", "WORKSPACE.members", ["WORKSPACE.api.remove.participant-count"], "group.sh", "test_remove_member", 144, 144, 'assert_eq "group has 1 participant after remove" "$count" "1"', "participants[]", "DELETE /groups/{group_id}/members/{bot_uuid}", "GET /groups/{group_id}.participants", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.api.patch.responds-name", "legacy group PATCH returns the new name", "WORKSPACE.patch", "WORKSPACE.patch", ["WORKSPACE.api.patch.responds-name"], "group.sh", "test_patch_group", 86, 87, 'assert_eq "legacy group PATCH returns the new name" \\\n        "$(json_field "$RESPONSE" "name")" "legacy-patch-e2e"', "name", "PATCH /groups/{group_id}.name", "PATCH response.name", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.api.patch.persists-label", "legacy group PATCH persists the label", "WORKSPACE.patch", "WORKSPACE.patch", ["WORKSPACE.api.patch.persists-label"], "group.sh", "test_patch_group", 90, 91, 'assert_eq "legacy group PATCH persists the label" \\\n        "$(json_field "$RESPONSE" "label")" "legacy-patch-e2e"', "label", "PATCH /groups/{group_id}.name", "GET /groups/{group_id}.label", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.api.patch.persists-context", "legacy group PATCH persists the context", "WORKSPACE.patch", "WORKSPACE.patch", ["WORKSPACE.api.patch.persists-context"], "group.sh", "test_patch_group", 92, 93, 'assert_eq "legacy group PATCH persists the context" \\\n        "$(json_field "$RESPONSE" "context")" "legacy-context-e2e"', "context", "PATCH /groups/{group_id}.context", "GET /groups/{group_id}.context", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.api.visibility.persists-public", "visibility is public", "WORKSPACE.visibility", "WORKSPACE.visibility", ["WORKSPACE.api.visibility.persists-public"], "group.sh", "test_update_group_visibility", 180, 180, 'assert_eq "visibility is public" "$vis" "public"', "visibility", "PUT /groups/{group_id}/visibility.visibility", "GET /groups/{group_id}.visibility", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.cli.fuse.error-payload-shape", "On nonzero bcs-cli fuse exit the payload must be structured JSON; a bare nonzero exit with no payload fails.", "WORKSPACE.cli-fuse", "WORKSPACE.cli-fuse", ["WORKSPACE.cli.fuse.exit-or-json"], "group.sh", "test_group_fuse_via_cli", 269, 273, 'if _cli_contains "$BCS_CLI_STDOUT" "{"; then\n            warn "fuse returned non-zero but JSON payload; accepting"\n            pass "fuse via CLI returned a payload"; TESTS_PASSED=$((TESTS_PASSED+1))\n        else\n            fail "fuse exited $BCS_CLI_EXIT with no payload"; TESTS_FAILED=$((TESTS_FAILED+1))', "error_payload", "bcs-cli fuse --group {group_id}", "CLI stdout JSON object", {"status": "nonzero", "shape": "json-object", "code": "fuse_error"}, "group_id+caller_bot"),
    ("WORKSPACE.tail.create.group-id", "incident response group has an id", "WORKSPACE.incident-group", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.create.group-id"], "stories.sh", "_story_group_metadata_without_workspace", 436, 436, 'assert_not_empty "incident response group has an id" "$group_id"', "id", "POST /groups.driver_bot+label+participants", "POST response.id", {"status": 200, "shape": "json", "code": None}, "group_id+driver_bot"),
    ("WORKSPACE.tail.read.keep-label", "incident response group keeps its label", "WORKSPACE.incident-group", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.read.keep-label"], "stories.sh", "_story_group_metadata_without_workspace", 441, 441, 'assert_json_eq "incident response group keeps its label" "$RESPONSE" "label" "Incident response room"', "label", "POST /groups.label", "GET /groups/{group_id}.label", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.tail.mute.mode", "participant mode is muted", "WORKSPACE.participant-mode", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.mute.mode"], "stories.sh", "_story_group_metadata_without_workspace", 445, 445, 'assert_json_eq "participant mode is muted" "$RESPONSE" "data.mode" "muted"', "data.mode", "PUT /groups/{group_id}/participants/{actor_id}/mode.mode", "PUT response.data.mode", {"status": 200, "shape": "json", "code": None}, "group_id+actor_id"),
    ("WORKSPACE.tail.mute.actor", "participant mode response identifies the actor", "WORKSPACE.participant-mode", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.mute.actor"], "stories.sh", "_story_group_metadata_without_workspace", 446, 446, 'assert_json_eq "participant mode response identifies the actor" "$RESPONSE" "data.actor_id" "$BOT_ENG_UUID"', "data.actor_id", "PUT /groups/{group_id}/participants/{actor_id}/mode.actor", "PUT response.data.actor_id", {"status": 200, "shape": "json", "code": None}, "group_id+actor_id"),
    ("WORKSPACE.tail.workspace.write-gone", "deprecated group workspace writes are unavailable", "WORKSPACE.workspace", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.workspace.write-gone"], "stories.sh", "_story_group_metadata_without_workspace", 450, 450, 'require_status "deprecated group workspace writes are unavailable" "404" || return', "workspace write", "PUT /groups/{group_id}/workspace", "error envelope", {"status": 404, "shape": "json", "code": "not_found"}, "group_id"),
    ("WORKSPACE.tail.workspace.read-gone", "deprecated group workspace reads are unavailable", "WORKSPACE.workspace", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.workspace.read-gone"], "stories.sh", "_story_group_metadata_without_workspace", 453, 453, 'require_status "deprecated group workspace reads are unavailable" "404" || return', "workspace read", "GET /groups/{group_id}/workspace", "error envelope", {"status": 404, "shape": "json", "code": "not_found"}, "group_id"),
    ("WORKSPACE.tail.routing.mode", "routing mode is persisted", "WORKSPACE.routing-service", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.routing.mode"], "stories.sh", "_story_group_metadata_without_workspace", 458, 458, 'assert_json_eq "routing mode is persisted" "$RESPONSE" "routing_policy.mode" "structured"', "routing_policy.mode", "PUT /groups/{group_id}/routing-policy.mode", "PUT response.routing_policy.mode", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.tail.routing.default-delivery", "default delivery is persisted", "WORKSPACE.routing-service", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.routing.default-delivery"], "stories.sh", "_story_group_metadata_without_workspace", 459, 459, 'assert_json_eq "default delivery is persisted" "$RESPONSE" "routing_policy.default_bot_final_delivery" "inject_observers"', "routing_policy.default_bot_final_delivery", "PUT /groups/{group_id}/routing-policy.default_bot_final_delivery", "PUT response.routing_policy.default_bot_final_delivery", {"status": 200, "shape": "json", "code": None}, "group_id"),
    ("WORKSPACE.tail.settings.gone-or-legacy", "retired group policy settings return Gone", "WORKSPACE.routing-service", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.settings.gone-or-legacy"], "stories.sh", "_story_group_metadata_without_workspace", 462, 462, 'require_status "retired group policy settings return Gone" "410" || return', "service_spec", "PATCH /groups/{group_id}/settings.service_spec", "error envelope", {"status": 410, "shape": "json", "code": "service_policy_retired"}, "group_id"),
    ("WORKSPACE.tail.settings.policy-identified", "retired policy is identified", "WORKSPACE.routing-service", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.settings.policy-identified"], "stories.sh", "_story_group_metadata_without_workspace", 463, 463, 'assert_json_eq "retired policy is identified" "$RESPONSE" "error" "service_policy_retired"', "error", "PATCH /groups/{group_id}/settings.service_spec", "response.error", {"status": 410, "shape": "json", "code": "service_policy_retired"}, "group_id"),
    ("WORKSPACE.tail.message.receives-id", "persistent message receives an id", "WORKSPACE.messages", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.message.receives-id"], "stories.sh", "_story_group_metadata_without_workspace", 468, 468, 'assert_json_not_empty "persistent message receives an id" "$RESPONSE" "message_id"', "message_id", "POST /groups/{group_id}/messages.message", "POST response.message_id", {"status": 200, "shape": "json", "code": None}, "group_id+message_id"),
    ("WORKSPACE.tail.message.records-routing", "persistent message records routing targets", "WORKSPACE.messages", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.message.records-routing"], "stories.sh", "_story_group_metadata_without_workspace", 469, 469, 'assert_json_not_empty "persistent message records routing targets" "$RESPONSE" "routed_to"', "routed_to", "POST /groups/{group_id}/messages.routing", "POST response.routed_to", {"status": 200, "shape": "json", "code": None}, "group_id+message_id"),
    ("WORKSPACE.tail.chat.identifies-group", "group chat response identifies the group", "WORKSPACE.messages", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.chat.identifies-group"], "stories.sh", "_story_group_metadata_without_workspace", 474, 474, 'assert_json_eq "group chat response identifies the group" "$RESPONSE" "group_id" "$group_id"', "group_id", "POST /groups/{group_id}/chat.message", "POST response.group_id", {"status": 200, "shape": "json", "code": None}, "group_id+message_id"),
    ("WORKSPACE.tail.chat.delivery-results", "group chat reports delivery results", "WORKSPACE.messages", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.chat.delivery-results"], "stories.sh", "_story_group_metadata_without_workspace", 475, 475, 'assert_json_not_empty "group chat reports delivery results" "$RESPONSE" "delivery_results"', "delivery_results", "POST /groups/{group_id}/chat.delivery_results", "POST response.delivery_results", {"status": 200, "shape": "json", "code": None}, "group_id+message_id"),
    ("WORKSPACE.tail.callback.identifies-group", "callback response identifies the group", "WORKSPACE.messages", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.callback.identifies-group"], "stories.sh", "_story_group_metadata_without_workspace", 480, 480, 'assert_json_eq "callback response identifies the group" "$RESPONSE" "group_id" "$group_id"', "group_id", "POST /groups/{group_id}/callback.message", "POST response.group_id", {"status": 200, "shape": "json", "code": None}, "group_id+callback_id"),
    ("WORKSPACE.tail.callback.delivery-results", "callback reports delivery results", "WORKSPACE.messages", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.callback.delivery-results"], "stories.sh", "_story_group_metadata_without_workspace", 481, 481, 'assert_json_not_empty "callback reports delivery results" "$RESPONSE" "delivery_results"', "delivery_results", "POST /groups/{group_id}/callback.delivery_results", "POST response.delivery_results", {"status": 200, "shape": "json", "code": None}, "group_id+callback_id"),
    ("WORKSPACE.tail.delete.confirms-id", "group deletion confirms the id", "WORKSPACE.incident-delete", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.delete.confirms-id"], "stories.sh", "_story_group_metadata_without_workspace", 485, 485, 'assert_json_eq "group deletion confirms the id" "$RESPONSE" "id" "$group_id"', "id", "DELETE /groups/{group_id}?bot_id={driver_bot}", "DELETE response.id", {"status": 200, "shape": "json", "code": None}, "group_id+driver_bot"),
    ("WORKSPACE.tail.delete.gone", "closed incident group is gone", "WORKSPACE.incident-delete", "WORKSPACE.tail-metadata", ["WORKSPACE.tail.delete.gone"], "stories.sh", "_story_group_metadata_without_workspace", 487, 487, 'require_status "closed incident group is gone" "404" || return', "deleted group", "GET /groups/{group_id}", "error envelope", {"status": 404, "shape": "json", "code": "not_found"}, "group_id"),
]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _baseline_rows() -> list[dict]:
    contract = json.loads(MIGRATION_CONTRACTS_PATH.read_text(encoding="utf-8"))
    story = next(row for row in contract["stories"] if row["legacy_id"] == STORY)
    result = []
    for row in story["requirements"]:
        result.append({
            "obligation_id": row["id"], "kind": "COARSE",
            "expectation": row["expectation"], "source_functions": row["functions"],
            "parent_obligation": None, "atom_ids": [],
            "field_name": None, "write_path": None, "read_path": None,
            "error_envelope": {"status": "source-defined", "shape": "source-defined", "code": None},
            "concurrency_isolation": "story-owned fixture resource",
        })
    if len(result) != BASELINE_COUNT:
        raise ValueError(f"baseline obligation count changed: {len(result)}")
    return result


def _field_rows() -> list[dict]:
    result = []
    for row in FIELD_ROWS:
        (oid, expectation, parent, group, atoms, source_file, function,
         line_start, line_end, quote, field_name, write_path, read_path,
         error_envelope, isolation) = row
        result.append({
            "obligation_id": oid, "kind": "FIELD", "expectation": expectation,
            "source_functions": [function], "parent_obligation": parent,
            "decomposition_group": group, "atom_ids": atoms,
            "field_name": field_name, "write_path": write_path,
            "read_path": read_path, "error_envelope": error_envelope,
            "concurrency_isolation": isolation,
            "predicate": ("fuse_error_payload_shape"
                          if oid == "WORKSPACE.cli.fuse.error-payload-shape"
                          else "atom_projection"),
            "source_file": source_file,
            "source_function": function, "line_start": line_start,
            "line_end": line_end, "source_quote": quote,
        })
    if len(result) != FIELD_COUNT:
        raise ValueError(f"field obligation count changed: {len(result)}")
    return result


def validate_and_emit(output_path: Path | None = None) -> dict:
    density = json.loads(DENSITY_CONTRACT_PATH.read_text(encoding="utf-8"))
    density_rows = density["density_requirements"]
    density_ids = {row["id"] for row in density_rows}
    baseline = _baseline_rows()
    field = _field_rows()
    baseline_rollup = {
        "WORKSPACE.create": ["WORKSPACE.api.create.status", "WORKSPACE.api.create.group-id", "WORKSPACE.api.members.create.status", "WORKSPACE.api.members.create.group-id", "WORKSPACE.api.members.detail.status", "WORKSPACE.api.members.detail.participant-count"],
        "WORKSPACE.detail": ["WORKSPACE.api.detail.status", "WORKSPACE.api.detail.group-id", "WORKSPACE.api.detail.driver-bot", "WORKSPACE.api.detail.group-kind", "WORKSPACE.api.detail.visibility"],
        "WORKSPACE.patch": ["WORKSPACE.api.patch.status", "WORKSPACE.api.patch.responds-name", "WORKSPACE.api.patch.persists-label", "WORKSPACE.api.patch.persists-context"],
        "WORKSPACE.list": ["WORKSPACE.api.list.status", "WORKSPACE.api.list.total-not-empty", "WORKSPACE.api.list.total-at-least-one"],
        "WORKSPACE.members": ["WORKSPACE.api.add.status", "WORKSPACE.api.add.participant-count", "WORKSPACE.api.remove.status", "WORKSPACE.api.remove.participant-count"],
        "WORKSPACE.label": ["WORKSPACE.api.label.status", "WORKSPACE.api.label.persists-label"],
        "WORKSPACE.visibility": ["WORKSPACE.api.visibility.status", "WORKSPACE.api.visibility.persists-public"],
        "WORKSPACE.cli-create-get": ["WORKSPACE.cli.create.exit-and-id", "WORKSPACE.cli.create.roundtrip", "WORKSPACE.cli.get.exit", "WORKSPACE.cli.get.id-and-driver"],
        "WORKSPACE.cli-add-member": ["WORKSPACE.cli.add.exit", "WORKSPACE.cli.add.echoes-member"],
        "WORKSPACE.cli-fuse": ["WORKSPACE.cli.fuse.exit-or-json"],
        "WORKSPACE.cli-status": ["WORKSPACE.cli.status.exit", "WORKSPACE.cli.status.reflects-completed", "WORKSPACE.cli.terminate.exit", "WORKSPACE.cli.terminate.reflects"],
        "WORKSPACE.incident-group": ["WORKSPACE.tail.create.status", "WORKSPACE.tail.create.group-id", "WORKSPACE.tail.read.status", "WORKSPACE.tail.read.keep-label"],
        "WORKSPACE.participant-mode": ["WORKSPACE.tail.mute.status", "WORKSPACE.tail.mute.mode", "WORKSPACE.tail.mute.actor"],
        "WORKSPACE.workspace": ["WORKSPACE.tail.workspace.write-gone", "WORKSPACE.tail.workspace.read-gone"],
        "WORKSPACE.routing-service": ["WORKSPACE.tail.routing.status", "WORKSPACE.tail.routing.mode", "WORKSPACE.tail.routing.default-delivery", "WORKSPACE.tail.settings.gone-or-legacy", "WORKSPACE.tail.settings.policy-identified"],
        "WORKSPACE.messages": ["WORKSPACE.tail.message.status", "WORKSPACE.tail.message.receives-id", "WORKSPACE.tail.message.records-routing", "WORKSPACE.tail.chat.status", "WORKSPACE.tail.chat.identifies-group", "WORKSPACE.tail.chat.delivery-results", "WORKSPACE.tail.callback.status", "WORKSPACE.tail.callback.identifies-group", "WORKSPACE.tail.callback.delivery-results"],
        "WORKSPACE.incident-delete": ["WORKSPACE.tail.delete.status", "WORKSPACE.tail.delete.confirms-id", "WORKSPACE.tail.delete.gone"],
    }
    for row in baseline:
        row["atom_ids"] = baseline_rollup[row["obligation_id"]]
    if set(sum(baseline_rollup.values(), [])) != density_ids or len(sum(baseline_rollup.values(), [])) != len(density_ids):
        raise ValueError("baseline atom rollup does not partition the 63 density atoms")
    for row in field:
        if row["parent_obligation"] not in baseline_rollup:
            raise ValueError("orphan FIELD parent: " + row["obligation_id"])
        if any(atom not in density_ids for atom in row["atom_ids"]):
            raise ValueError("FIELD atom not in frozen density table: " + row["obligation_id"])
        source = (E2E_TEST_ROOT / row["source_file"]).read_text(encoding="utf-8")
        if source.count(row["source_quote"]) != 1:
            raise ValueError("source quote not unique: " + row["obligation_id"])
    all_rows = baseline + field
    if len(all_rows) != TOTAL_COUNT or len({row["obligation_id"] for row in all_rows}) != TOTAL_COUNT:
        raise ValueError("workspace obligation shape changed")
    payload = {
        "schema_version": 1, "contract": CONTRACT, "parent_story": STORY,
        "obligation_counts": {"baseline": BASELINE_COUNT, "field": FIELD_COUNT, "total": TOTAL_COUNT},
        "atom_count": density["atom_count"], "atom_contract": "group-workspace-density-contract.json",
        "baseline_atom_rollup": baseline_rollup,
        "obligations": all_rows,
        "sources": {"group.sh": _sha256(E2E_TEST_ROOT / "group.sh"), "stories.sh": _sha256(E2E_TEST_ROOT / "stories.sh"), "migration-contracts.json": _sha256(MIGRATION_CONTRACTS_PATH), "group-workspace-density-contract.json": _sha256(DENSITY_CONTRACT_PATH)},
        "dual_shape": {"local": {"executor": "owned-local", "validation": "OFFLINE_FAKE_TRANSPORT", "remote_validation": "NOT_RUN"}, "preprod": {"profiles": ["human-cookie", "owned-bot"], "validation": "NOT_RUN_RECEIPT", "remote_validation": "NOT_RUN"}},
        "release_credit": {"grants_release_credit": False, "preprod_live": "NOT_RUN"},
        "field_net_inflow": {"extracted_clauses_total": 43, "field_rows": FIELD_COUNT, "already_registered_as_baseline": BASELINE_COUNT, "plumbing_excluded": ["status guards", "implicit CLI wrapper exit", "empty-id setup guards", "cleanup return"]},
        "upstream_mapping_note": "The 63 atom table is the evidence oracle. The 43 obligations are a coarse 17-row roll-up plus 26 source-anchored FIELD rows; duplicate status/plumbing clauses are retained in the atom roll-up but not counted as new FIELD obligations.",
    }
    destination = output_path or OUTPUT_PATH
    destination.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    built = validate_and_emit()
    print(json.dumps({"wrote": str(OUTPUT_PATH), "obligations": built["obligation_counts"], "atom_count": built["atom_count"]}, ensure_ascii=False))
