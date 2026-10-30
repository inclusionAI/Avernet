"""Field-level Workspace obligation projection over the 63-atom oracle.

The module is intentionally offline.  It consumes retained density journals or
an in-memory fake transport and never opens a socket, resolves credentials, or
claims preproduction execution.  The baseline 17 obligations and 26 FIELD
rows are two views of the same 63 atoms; FIELD rows do not invent a second
assertion vocabulary.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..group_workspace_density import (
    CONTRACT_PATH as DENSITY_CONTRACT_PATH,
    JOURNALS,
    load_group_workspace_density_contract,
    project_density,
    workspace_density_bindings,
)
from ..run_state import RunStateError

CONTRACT_PATH = Path(__file__).resolve().parent / "workspace-field-obligations-contract.json"
CONTRACT_ID = "workspace-field-obligations-v1"
PARENT_STORY = "story_user_operates_group_workspace"
BASELINE_COUNT = 17
FIELD_COUNT = 26
OBLIGATION_COUNT = 43
VALID_STATUS = ("PASS", "FAIL", "NOT_RUN")


def load_workspace_field_contract(path: Path | None = None) -> dict:
    return json.loads((path or CONTRACT_PATH).read_text(encoding="utf-8"))


def validate_workspace_field_contract(contract: dict) -> dict:
    if contract.get("schema_version") != 1 or contract.get("contract") != CONTRACT_ID:
        raise RunStateError("Workspace field contract identity changed")
    if contract.get("parent_story") != PARENT_STORY:
        raise RunStateError("Workspace field contract parent changed")
    counts = contract.get("obligation_counts") or {}
    if counts != {"baseline": BASELINE_COUNT, "field": FIELD_COUNT, "total": OBLIGATION_COUNT}:
        raise RunStateError("Workspace obligation counts changed")
    rows = contract.get("obligations")
    if not isinstance(rows, list) or len(rows) != OBLIGATION_COUNT:
        raise RunStateError("Workspace obligation table changed")
    ids = [row.get("obligation_id") for row in rows]
    if len(set(ids)) != OBLIGATION_COUNT or not all(isinstance(item, str) for item in ids):
        raise RunStateError("Workspace obligation ids changed")
    if contract.get("atom_count") != 63:
        raise RunStateError("Workspace atom count must remain 63")
    baseline = [row for row in rows if row.get("kind") == "COARSE"]
    fields = [row for row in rows if row.get("kind") == "FIELD"]
    if len(baseline) != BASELINE_COUNT or len(fields) != FIELD_COUNT:
        raise RunStateError("Workspace coarse/field decomposition changed")
    density = load_group_workspace_density_contract(DENSITY_CONTRACT_PATH)
    atom_ids = {row["id"] for row in density["density_requirements"]}
    rollup = contract.get("baseline_atom_rollup") or {}
    flattened = [atom for atoms in rollup.values() for atom in atoms]
    if len(flattened) != 63 or set(flattened) != atom_ids or len(set(flattened)) != 63:
        raise RunStateError("Workspace baseline roll-up does not partition 63 atoms")
    for row in fields:
        if row.get("parent_obligation") not in rollup:
            raise RunStateError("Workspace FIELD parent is not a baseline obligation")
        if not row.get("field_name") or not row.get("write_path") or not row.get("read_path"):
            raise RunStateError("Workspace FIELD row lost field/read-write metadata")
        if not isinstance(row.get("error_envelope"), dict) or not row.get("concurrency_isolation"):
            raise RunStateError("Workspace FIELD row lost error/isolation metadata")
        if not set(row.get("atom_ids") or {}).issubset(atom_ids):
            raise RunStateError("Workspace FIELD row references an unknown atom")
    return contract


def workspace_field_profile(*, tier: str) -> dict:
    """Return a credential-free local/preprod profile contract."""
    if tier not in {"local", "preprod"}:
        raise RunStateError("Workspace profile tier must be local or preprod")
    if tier == "local":
        return {
            "contract": "dual-workspace-field-profile-v1",
            "environment": {"tier": "local", "id": "workspace-field-local"},
            "executor": {"kind": "owned-local", "transport": "fake-offline"},
            "entrypoint": {"kind": "singlebox", "api_face": "legacy", "cli_face": "product"},
            "auth": {"kind": "owned_local_bot_token", "token_env": "BCS_E2E_DRIVER_TOKEN"},
            "remote_validation": "NOT_RUN",
        }
    return {
        "contract": "dual-workspace-field-profile-v1",
        "environment": {"tier": "preprod", "id": "workspace-field-preprod"},
        "executor": {"kind": "owned-local", "transport": "fake-offline"},
        "entrypoint": {"kind": "gateway", "api_face": "legacy", "cli_face": "product"},
        "auth": {
            "profiles": [
                {"kind": "human-cookie", "cookie_env": "BCS_E2E_HUMAN_COOKIE"},
                {"kind": "owned_bot", "token_env": "BCS_E2E_DRIVER_TOKEN"},
            ]
        },
        "remote_validation": "NOT_RUN",
    }


def validate_workspace_field_profile(profile: dict) -> dict:
    if not isinstance(profile, dict) or profile.get("contract") != "dual-workspace-field-profile-v1":
        raise RunStateError("Workspace dual profile identity changed")
    environment = profile.get("environment") or {}
    tier = environment.get("tier")
    if tier not in {"local", "preprod"}:
        raise RunStateError("Workspace dual profile tier is invalid")
    if profile.get("executor", {}).get("kind") != "owned-local":
        raise RunStateError("Workspace executor must stay owned-local")
    if profile.get("remote_validation") != "NOT_RUN":
        raise RunStateError("Workspace profile may not claim remote validation")
    if tier == "preprod":
        kinds = {item.get("kind") for item in profile.get("auth", {}).get("profiles", [])}
        if kinds != {"human-cookie", "owned_bot"}:
            raise RunStateError("Preprod profile must retain both auth profiles")
    else:
        if profile.get("auth", {}).get("kind") != "owned_local_bot_token":
            raise RunStateError("Local profile must use the owned-local auth kind")
    return profile


def _set(target: dict, dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    for part in parts[:-1]:
        target = target.setdefault(part, {})
    target[parts[-1]] = value


def _resolve_expected(expected: Any, bindings: dict) -> Any:
    if isinstance(expected, dict) and set(expected) == {"binding"}:
        value: Any = bindings
        for part in expected["binding"].split("."):
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
        return value
    return expected


def fake_workspace_journals() -> dict:
    """Build deterministic evidence for the local offline executor only."""
    contract = load_group_workspace_density_contract()
    bindings = {
        "primary": "grp-fake-primary", "members": "grp-fake-members",
        "cli_primary": "grp-fake-cli", "cli_termination": "grp-fake-term",
        "incident": "grp-fake-incident",
        "roles": {"CEO": "bot-fake-ceo", "PM": "bot-fake-pm", "ENG": "bot-fake-eng", "QA": "bot-fake-qa"},
    }
    journals = {name: {"operations": []} for name in JOURNALS}
    journals["api"]["resources"] = {
        "group-create-primary": {"id": bindings["primary"]},
        "group-create-with-members": {"id": bindings["members"]},
    }
    journals["cli"]["resources"] = {
        "primary": {"id": bindings["cli_primary"]},
        "termination": {"id": bindings["cli_termination"]},
    }
    journals["incident"]["resources"] = {"incident": {"id": bindings["incident"]}}
    journals["messages"]["group_id"] = bindings["incident"]
    journals["delete"]["group_id"] = bindings["incident"]

    for row in contract["density_requirements"]:
        address = row["address"]
        if address["kind"] == "operation":
            event = next((item for item in journals[address["journal"]]["operations"]
                          if item.get("operation") == address["operation"]), None)
            if event is None:
                event = {"operation": address["operation"], "status": "ACKNOWLEDGED"}
                journals[address["journal"]]["operations"].append(event)
            for check in row["checks"]:
                kind = check["type"]
                if kind == "status_eq" or kind == "not_found_state":
                    event["http_status"] = check["value"]
                elif kind == "exit_zero":
                    event["cli_exit_code"] = 0
                elif kind == "field_eq":
                    _set(event, check["path"], _resolve_expected(check["expected"], bindings))
                elif kind == "field_not_empty":
                    _set(event, check["path"], "fake-non-empty")
                elif kind == "stdout_contains":
                    event["stdout"] = (event.get("stdout") or "") + str(_resolve_expected(check["value"], bindings))
                elif kind == "stdout_contains_any":
                    event["stdout"] = (event.get("stdout") or "") + check["values"][0]
                elif kind == "stdout_regex":
                    event["stdout"] = "  ID: " + bindings["cli_primary"]
                elif kind == "any_of":
                    # The local fake follows the normal successful branch.
                    event["cli_exit_code"] = 0
                elif kind in {"settings_retired_form", "settings_policy_form"}:
                    event["http_status"] = 410
                    event["value"] = {"error": "service_policy_retired"}
        elif address["kind"] == "read":
            path = address["path_template"].replace("{primary}", bindings["primary"]).replace("{members}", bindings["members"])
            if path == "/groups?list-all":
                path = "/groups?offset=0&limit=50&group_kind=all"
            entries = journals[address["journal"]].setdefault("reads", [])
            entry = next((item for item in entries
                          if item.get("path") == path
                          and item.get("after_operation") == address.get("after_operation")), None)
            if entry is None:
                entry = {"method": "GET", "path": path, "after_operation": address.get("after_operation"), "value": {}}
                entries.append(entry)
            for check in row["checks"]:
                kind = check["type"]
                if kind == "read_field_eq":
                    _set(entry["value"], check["path"], _resolve_expected(check["expected"], bindings))
                elif kind == "read_field_not_empty":
                    _set(entry["value"], check["path"], "fake-non-empty")
                elif kind == "read_field_len_eq":
                    _set(entry["value"], check["path"], ["fake"] * check["value"])
                elif kind == "read_field_ge":
                    _set(entry["value"], check["path"], check["value"])
        else:
            journals[address["journal"]][address["state_key"]] = {"status": "PASS", "http_status": 404, "group_id": bindings["incident"]}
    return journals


class FakeWorkspaceTransport:
    """Socket-free transport used by focused tests and offline local receipts."""

    def __init__(self, profile: dict):
        self.profile = validate_workspace_field_profile(profile)
        self.calls: list[dict] = []

    def execute(self) -> dict:
        self.calls.append({"transport": "fake-offline", "operation": "workspace-density"})
        if self.profile["environment"]["tier"] == "preprod":
            return {
                "schema_version": 1, "status": "NOT_RUN", "obligation_count": OBLIGATION_COUNT,
                "remote_validation": "NOT_RUN", "grants_release_credit": False,
                "reason": "preprod live execution was not requested/admitted",
                "profile_id": self.profile["environment"]["id"], "calls": len(self.calls),
            }
        journals = fake_workspace_journals()
        bindings = workspace_density_bindings(None, journals, roles={
            "CEO": "bot-fake-ceo", "PM": "bot-fake-pm", "ENG": "bot-fake-eng", "QA": "bot-fake-qa"})
        density = project_density(load_group_workspace_density_contract(), journals, bindings)
        return {
            "schema_version": 1, "status": "PASS", "remote_validation": "NOT_RUN",
            "grants_release_credit": False, "profile_id": self.profile["environment"]["id"],
            "density": density, "journals": journals, "calls": len(self.calls),
        }


def _rollup_status(rows: list[dict]) -> str:
    statuses = {row["status"] for row in rows}
    if "FAIL" in statuses:
        return "FAIL"
    if statuses == {"PASS"}:
        return "PASS"
    return "NOT_RUN"


def project_workspace_obligations(contract: dict, density: dict, *, profile: dict) -> dict:
    """Project 17 coarse + 26 FIELD rows without changing atom semantics."""
    validate_workspace_field_contract(contract)
    validate_workspace_field_profile(profile)
    atom_rows = {row["id"]: row for row in density.get("requirements", [])}
    if len(atom_rows) != 63:
        raise RunStateError("Workspace density evidence does not contain 63 atoms")
    rows = []
    for obligation in contract["obligations"]:
        atom_evidence = [atom_rows[atom] for atom in obligation["atom_ids"]]
        status = _rollup_status(atom_evidence)
        observed = {key: sum(item["status"] == key for item in atom_evidence) for key in VALID_STATUS}
        if obligation.get("predicate") == "fuse_error_payload_shape":
            # The upstream branch is only exercised after a non-zero fuse exit.
            # A normal exit is valid for the 63-atom oracle but is honest NOT_RUN
            # for this stricter FIELD decomposition.
            status = "NOT_RUN" if status == "PASS" else status
            observed = "strict_error_branch_not_exercised" if status == "NOT_RUN" else observed
        rows.append({
            "obligation_id": obligation["obligation_id"], "kind": obligation["kind"],
            "parent_obligation": obligation.get("parent_obligation"), "field_name": obligation.get("field_name"),
            "status": status, "atom_ids": list(obligation["atom_ids"]), "atom_counts": observed,
            "source_file": obligation.get("source_file"), "source_function": obligation.get("source_function"),
            "read_path": obligation.get("read_path"), "write_path": obligation.get("write_path"),
            "error_envelope": obligation.get("error_envelope"),
            "concurrency_isolation": obligation.get("concurrency_isolation"),
            "predicate": obligation.get("predicate", "atom_projection"),
            "observed": observed,
        })
    counts = {status: sum(row["status"] == status for row in rows) for status in VALID_STATUS}
    baseline_counts = {status: sum(row["kind"] == "COARSE" and row["status"] == status for row in rows) for status in VALID_STATUS}
    field_counts = {status: sum(row["kind"] == "FIELD" and row["status"] == status for row in rows) for status in VALID_STATUS}
    verdict = "BLOCKED" if counts["FAIL"] else ("PASS" if counts["PASS"] == OBLIGATION_COUNT else "INCOMPLETE")
    return {
        "schema_version": 1, "contract": CONTRACT_ID, "parent": PARENT_STORY,
        "status": verdict, "obligation_count": OBLIGATION_COUNT, "counts": counts,
        "baseline_counts": baseline_counts, "field_counts": field_counts,
        "obligations": rows, "atom_contract": "group-workspace-density-contract.json",
        "atom_count": 63, "remote_validation": "NOT_RUN", "grants_release_credit": False,
        "profile_id": profile["environment"]["id"],
    }


def preprod_not_run_receipt(profile: dict | None = None) -> dict:
    profile = validate_workspace_field_profile(profile or workspace_field_profile(tier="preprod"))
    if profile["environment"]["tier"] != "preprod":
        raise RunStateError("preprod receipt requires the preprod profile")
    return {
        "schema_version": 1, "contract": CONTRACT_ID, "parent": PARENT_STORY,
        "status": "NOT_RUN", "obligation_count": OBLIGATION_COUNT,
        "counts": {"PASS": 0, "FAIL": 0, "NOT_RUN": OBLIGATION_COUNT},
        "remote_validation": "NOT_RUN", "grants_release_credit": False,
        "profile_id": profile["environment"]["id"],
        "reason": "preprod leg retained as a dual profile contract; no live execution in this task",
    }
