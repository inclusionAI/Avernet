"""Offline-computable owned-local arm for the Wave-D3 density overlay.

The density overlay rides families whose owned-local journeys are already
recorded green: the four write-domain parents through the frozen
``dual-edge-friend-mutation-run`` face, and the read parent through the frozen
``dual-edge-read-run`` face.  This module never re-executes any journey and
never opens a transport.  It renders one owned-local arm per density case with
four fail-closed pillars:

1. **frozen binding** — the shared family source binding for the exact case
   slice (parent identity, frozen requirement ids, source revision digest and
   the self-consistent ``edge_permission.sh`` digest), plus the WRITE_ANNEX
   seam ownership note for the read parent whose POST create stays outside the
   frozen read slice;
2. **seam presence** — offline verification that the owned-local adapter
   modules still expose every frozen seam the 22 density leaves reference, plus
   the two frozen read-slice seams the read parent rides on;
3. **seam literals** — class-scoped verification that the upstream assertion
   text each leaf pins still appears verbatim in the owner class source; adjacent
   implicitly-concatenated string literals are folded through AST so a literal
   split across source lines is not misreported as drift;
4. **recorded owned-local round** — when a caller attaches the evidence root,
   the recorded wpc owned-local round (junit / flow report / suite summary) is
   re-read offline and verified by content and digest; the read parent is
   disclosed ``LEG_ABSENT`` because its frozen slice belongs to the
   ``dual-edge-read-run`` family, not to this mutation round.

Pillar one fails closed with ``RunStateError`` on registry drift; pillars two
through four report contradictions as an honest conflict ledger and map the arm
to ``LOCAL_MODEL_CONFLICT`` instead of silently trusting any side.
"""
from __future__ import annotations

import ast
import hashlib
import importlib
import inspect
import json
import xml.etree.ElementTree as ElementTree
from pathlib import Path
from typing import Any

from ..edge_friend_mutation_case import (
    edge_friend_mutation_case_plan,
    edge_friend_mutation_source_binding,
)
from ..edge_read_contract import (
    FRIEND_CONNECTIONS_PARENT,
    friend_connections_list_source_binding,
)
from ..run_state import RunStateError
from .density_contract import (
    DENSITY_CASE_KEYS,
    LOCAL_MODEL_CONFLICT,
    LOCAL_MODEL_READY,
    MUTATION_FAMILY_KIND,
    SOURCE_FAMILY_SUITE,
    density_case_row,
)
from .density_obligations import (
    FROZEN_MUTATION_CASE_IDS,
    FROZEN_READ_CASE_ID,
    FROZEN_READ_PUBLIC_CASE_ID,
    FROZEN_READ_REQUIREMENTS,
    PARENTS,
    build_density_obligations,
    source_digest,
)

JUNIT_FILE = "junit.xml"
FLOW_REPORT_FILE = "edge-friend-mutations-flow-report.json"
SUMMARY_FILE = "summary.json"
WPC_ROUND_DIR_CANDIDATES = (
    "20261031-migration-wpc-local/owned-local",
    "owned-local",
    "",
)
READ_SEAM_MODULE = "bcs_e2e.owned_edge_read_adapter"
READ_SEAM_METHODS = ("read_friend_connections_list",
                     "read_friend_connections_list_repeat")
DUAL_ENVIRONMENT_ADAPTER_FAMILIES = (
    "OwnedLocalCaseAdapter", "SingleboxCaseAdapter", "RemoteCaseAdapter",
)
ANNEX_SEAM_NOTE = (
    "the POST create stays outside the frozen EDGE-FRIEND-CONNECTIONS read "
    "slice; the density WRITE_ANNEX leaf carries a mutation-family seam "
    "reference so the annex is verified against the owned-local mutation "
    "adapter, never against the read adapter")


# --------------------------------------------------------------------------- #
# pillar 1 — frozen family binding projection
# --------------------------------------------------------------------------- #

def frozen_binding_projection(case_key: str) -> dict[str, Any]:
    """Project and verify the frozen family binding for one density case."""
    row = density_case_row(case_key)
    conflicts: list[str] = []
    projection: dict[str, Any] = {
        "case_key": case_key,
        "case_id": row["case_id"],
        "public_case_id": row.get("public_case_id"),
        "parent_id": row["parent_id"],
        "family_kind": row["family_kind"],
        "frozen_requirement_ids": list(row["frozen_requirement_ids"]),
        "seam_methods": list(row["seam_methods"]),
    }
    if row["family_kind"] == MUTATION_FAMILY_KIND:
        binding = edge_friend_mutation_source_binding(row["case_id"])
        plan = edge_friend_mutation_case_plan(row["case_id"])
        requirements = list(binding.get("requirements") or [])
        requirement_ids = [item["id"] for item in requirements
                           if isinstance(item, dict)]
        adapter_method = requirements[0].get("adapter_method") if requirements else None
        if binding.get("parent") != row["parent_id"]:
            conflicts.append("mutation source binding parent drifted from the frozen plan")
        if binding.get("source_revision") != (binding.get("sources") or {}).get("edge_permission.sh"):
            conflicts.append("mutation source binding revision is not the edge_permission.sh digest")
        if requirement_ids != list(row["frozen_requirement_ids"]):
            conflicts.append("mutation source binding requirements drifted from the frozen plan")
        if adapter_method not in row["seam_methods"]:
            conflicts.append("mutation source binding adapter method left the frozen seam")
        for environment in plan.environments:
            if environment.adapter_family not in DUAL_ENVIRONMENT_ADAPTER_FAMILIES:
                conflicts.append("mutation plan environment left the dual family")
        projection.update({
            "binding": {
                "source_revision": binding.get("source_revision"),
                "sources": dict(binding.get("sources") or {}),
                "requirements": [
                    {"id": item.get("id"), "adapter_method": item.get("adapter_method")}
                    for item in requirements],
            },
            "plan_environments": [
                {"name": environment.name, "adapter_family": environment.adapter_family}
                for environment in plan.environments],
        })
    else:
        binding = friend_connections_list_source_binding()
        requirement_ids = [item["id"] for item in binding.get("requirements") or []
                           if isinstance(item, dict)]
        if binding.get("parent") != FRIEND_CONNECTIONS_PARENT:
            conflicts.append("read source binding parent drifted from the frozen map")
        if tuple(requirement_ids) != tuple(FROZEN_READ_REQUIREMENTS):
            conflicts.append("read source binding requirements drifted from the frozen map")
        if (binding.get("sources") or {}).get("edge_permission.sh") != source_digest():
            conflicts.append("read source binding edge_permission.sh digest drifted")
        projection.update({
            "binding": {
                "source_revision": binding.get("source_revision"),
                "sources": dict(binding.get("sources") or {}),
                "requirements": [{"id": item} for item in requirement_ids],
            },
            "annex_seam": {
                "module": "bcs_e2e.local_edge_friend_mutations_case",
                "method": "_create_request",
                "note": ANNEX_SEAM_NOTE,
            },
        })
    projection["conflicts"] = conflicts
    return projection


# --------------------------------------------------------------------------- #
# pillar 2 — seam presence (offline module scan)
# --------------------------------------------------------------------------- #

def _seam_module(module_name: str):
    try:
        return importlib.import_module(module_name)
    except Exception:  # an import failure is an offline finding, not a crash
        return None


def _owner_class(module: Any, method: str):
    """Return the class that defines ``method`` in its own dict."""
    if module is None:
        return None
    for candidate in vars(module).values():
        if inspect.isclass(candidate) and method in vars(candidate):
            return candidate
    return None


def _seam_presence(module_name: str, methods: tuple[str, ...]) -> dict[str, Any]:
    module = _seam_module(module_name)
    rows = []
    verified = module is not None
    for method in methods:
        owner = _owner_class(module, method) if module is not None else None
        callable_seam = owner is not None and callable(getattr(owner, method, None))
        rows.append({
            "method": method,
            "owner_class": owner.__name__ if owner is not None else None,
            "callable": bool(callable_seam),
        })
        verified = verified and callable_seam
    return {
        "module": module_name,
        "methods": rows,
        "verified": bool(verified),
        "basis": (
            "offline module scan for the frozen seam names as own class "
            "attributes; no transport, no process and no fixture write"),
    }


def seam_presence_projection(leaves: tuple[Any, ...]) -> dict[str, Any]:
    """Verify every seam the 22 density leaves reference, plus the read pair."""
    modules: dict[str, set[str]] = {}
    for leaf in leaves:
        seam = leaf.seam
        modules.setdefault(seam["module"], set()).add(seam["method"])
    rows = {}
    verified = True
    for module_name in sorted(modules):
        presence = _seam_presence(module_name, tuple(sorted(modules[module_name])))
        rows[module_name] = presence
        verified = verified and presence["verified"]
    read_presence = _seam_presence(READ_SEAM_MODULE, READ_SEAM_METHODS)
    rows[READ_SEAM_MODULE] = read_presence
    verified = verified and read_presence["verified"]
    return {
        "modules": rows,
        "verified": bool(verified),
        "read_family_methods": list(READ_SEAM_METHODS),
        "basis": (
            "the 22 density leaves reference the owned-local mutation seams; "
            "the frozen read slice additionally rides the two owned-local read "
            "seams (disclosed for case friend-request-and-list)"),
    }


# --------------------------------------------------------------------------- #
# pillar 3 — class-scoped literal verification
# --------------------------------------------------------------------------- #

def _folded_string_constants(source: str) -> str:
    """Fold every string constant in one class source into one buffer."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    return "".join(
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str))


def _literal_posture(owner_class: Any, literals: tuple[str, ...]) -> dict[str, Any]:
    try:
        source = inspect.getsource(owner_class)
    except (OSError, TypeError):
        source = ""
    missing_raw = [literal for literal in literals if literal not in source]
    if not missing_raw:
        return {"posture": "SOURCE_LITERAL", "missing": []}
    folded = _folded_string_constants(source)
    missing = [literal for literal in missing_raw if literal not in folded]
    if not missing:
        return {"posture": "FOLDED_STRING_CONSTANTS", "missing": []}
    return {"posture": "LITERAL_DRIFT", "missing": missing}


def seam_literal_rows(leaves: tuple[Any, ...]) -> list[dict[str, Any]]:
    """Verify per-leaf seam literals against the owner class source."""
    rows = []
    for leaf in leaves:
        seam = leaf.seam
        module = _seam_module(seam["module"])
        owner = _owner_class(module, seam["method"])
        posture = _literal_posture(owner, tuple(seam.get("literals") or ()))
        if owner is None:
            posture = {"posture": "OWNER_CLASS_ABSENT", "missing": list(seam.get("literals") or ())}
        rows.append({
            "obligation_id": leaf.obligation_id,
            "seam_module": seam["module"],
            "seam_method": seam["method"],
            "owner_class": owner.__name__ if owner is not None else None,
            "literal_count": len(seam.get("literals") or ()),
            "posture": posture["posture"],
            "missing_literals": posture["missing"],
        })
    return rows


# --------------------------------------------------------------------------- #
# pillar 4 — recorded owned-local wpc round (offline re-read)
# --------------------------------------------------------------------------- #

def resolve_local_round_root(evidence_root: Path) -> Path:
    """Accept a dual round root, the wpc round directory, or owned-local."""
    root = Path(evidence_root)
    candidate = root / "20261031-migration-wpc-local" / "owned-local"
    if candidate.is_dir() and not candidate.is_symlink():
        return candidate
    candidate = root / "owned-local"
    if candidate.is_dir() and not candidate.is_symlink():
        return candidate
    return root


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _junit_rows(junit_path: Path, frozen_case_ids: tuple[str, ...],
                conflicts: list[str]) -> dict[str, Any]:
    try:
        tree = ElementTree.parse(junit_path)
    except (OSError, ElementTree.ParseError) as error:
        conflicts.append("wpc junit.xml unreadable: " + type(error).__name__)
        return {"cases": {}, "all_pass": False}
    cases: dict[str, dict[str, str]] = {}
    for testcase in tree.iter("testcase"):
        name = testcase.get("name") or ""
        properties = {}
        properties_node = testcase.find("properties")
        if properties_node is not None:
            for prop in properties_node.findall("property"):
                properties[prop.get("name")] = prop.get("value")
        cases[name] = {
            "runner_status": properties.get("runner_status"),
            "case_version": properties.get("case_version"),
            "functional_status": properties.get("functional_status"),
            "evidence_status": properties.get("evidence_status"),
            "cleanup_status": properties.get("cleanup_status"),
            "resource_disposition": properties.get("resource_disposition"),
            "manifest_digest": properties.get("manifest_digest"),
            "executor_digest": properties.get("executor_digest"),
        }
    all_pass = bool(cases)
    for name, recorded in cases.items():
        if recorded["runner_status"] != "PASS" or recorded["case_version"] != "1":
            conflicts.append("wpc junit row drifted for " + name)
            all_pass = False
        for field in ("functional_status", "evidence_status", "cleanup_status",
                      "resource_disposition", "manifest_digest", "executor_digest"):
            if not recorded[field]:
                conflicts.append("wpc junit row " + name + " lacks " + field)
                all_pass = False
    for case_id in frozen_case_ids:
        if case_id not in cases:
            conflicts.append("wpc junit lacks the frozen case row " + case_id)
            all_pass = False
    return {"cases": cases, "all_pass": all_pass}


def _flow_row(flow_path: Path, frozen_case_ids: tuple[str, ...],
              conflicts: list[str]) -> dict[str, Any]:
    try:
        raw = json.loads(flow_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        conflicts.append("wpc flow report unreadable: " + type(error).__name__)
        return {"cases": {}, "read_case_absent": None}
    cases = []
    for row in raw.get("cases") or []:
        cases.append({
            "case_id": row.get("case_id"),
            "parent_id": row.get("parent_id"),
            "status": row.get("status"),
            "completed_count": row.get("completed_count"),
            "total_count": row.get("total_count"),
        })
    for case_id in frozen_case_ids:
        rows = [item for item in cases if item["case_id"] == case_id]
        if len(rows) != 1:
            conflicts.append("wpc flow report lacks exactly one row for " + case_id)
            continue
        recorded = rows[0]
        if recorded["status"] != "PASS":
            conflicts.append("wpc flow row status is not PASS for " + case_id)
        if recorded["completed_count"] != recorded["total_count"]:
            conflicts.append("wpc flow row counts are not converged for " + case_id)
    credentials = raw.get("credentials") or {}
    read_local = FROZEN_READ_CASE_ID in {item["case_id"] for item in cases}
    read_public = FROZEN_READ_PUBLIC_CASE_ID in {item["case_id"] for item in cases}
    if raw.get("status") != "LOCAL_EDGE_FRIEND_MUTATIONS_PASS":
        conflicts.append("wpc flow suite status drifted")
    if raw.get("suite") != SOURCE_FAMILY_SUITE:
        conflicts.append("wpc flow suite drifted from the owned-local family")
    if raw.get("remote_validation") != "NOT_RUN":
        conflicts.append("wpc local round claims remote validation")
    if credentials.get("state") != "CLOSED":
        conflicts.append("wpc local round credential state is not CLOSED")
    return {
        "cases": cases,
        "run_id": raw.get("run_id"),
        "suite_status": raw.get("status"),
        "suite": raw.get("suite"),
        "read_case_absent": not (read_local or read_public),
    }


def _summary_row(summary_path: Path, conflicts: list[str]) -> dict[str, Any]:
    try:
        raw = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        conflicts.append("wpc summary unreadable: " + type(error).__name__)
        return {}
    integrity = raw.get("integrity") or {}
    scope = raw.get("scope") or {}
    if raw.get("status") != "LOCAL_SUITE_PASS":
        conflicts.append("wpc summary status is not LOCAL_SUITE_PASS")
    if integrity.get("status") != "PASS":
        conflicts.append("wpc summary integrity is not PASS")
    if scope.get("suite") != SOURCE_FAMILY_SUITE:
        conflicts.append("wpc summary scope suite drifted")
    return {
        "run_id": raw.get("run_id"),
        "status": raw.get("status"),
        "suite": scope.get("suite"),
        "integrity_status": integrity.get("status"),
        "selected_read_case_ids": [
            item for item in (scope.get("selected_cases") or [])
            if "FRIEND-CONNECTIONS-LIST" in str(item)],
    }


def recorded_owned_local_round(evidence_root: Path | None) -> dict[str, Any]:
    """Re-read the recorded wpc owned-local round without re-executing."""
    if evidence_root is None:
        return {
            "availability": "NOT_ATTACHED",
            "basis": (
                "the recorded wpc owned-local round is not attached to this arm; "
                "the offline projection stands on the frozen binding and the "
                "owned-local seams alone"),
            "conflicts": [],
        }
    root = resolve_local_round_root(Path(evidence_root))
    conflicts: list[str] = []
    if root.is_symlink() or not root.is_dir():
        return {
            "availability": "ROOT_UNAVAILABLE",
            "evidence_root": str(root),
            "basis": "attached evidence root is not a directory; conflicts recorded",
            "conflicts": ["owned-local round root is not an existing directory"],
        }
    files: dict[str, Any] = {}
    for name in (JUNIT_FILE, FLOW_REPORT_FILE, SUMMARY_FILE):
        candidate = root / name
        if candidate.is_symlink() or not candidate.is_file():
            conflicts.append("recorded owned-local round lacks " + name)
            continue
        files[name] = {"digest": _file_digest(candidate), "bytes": candidate.stat().st_size}
    if not files:
        return {
            "availability": "ROUND_ABSENT",
            "evidence_root": str(root),
            "wpc_dir_candidates": list(WPC_ROUND_DIR_CANDIDATES),
            "suite": SOURCE_FAMILY_SUITE,
            "files": {},
            "read_slice_disposition": "LEG_ABSENT",
            "basis": (
                "the attached evidence root is a real directory but none of "
                "the three recorded owned-local round artifacts is present; "
                "the absence is disclosed here and never charged as a local "
                "model conflict, so the offline projection stands on the "
                "frozen binding and the owned-local seams alone"),
            "conflicts": [],
        }
    frozen_ids = tuple(FROZEN_MUTATION_CASE_IDS[parent] for parent in PARENTS
                       if parent in FROZEN_MUTATION_CASE_IDS)
    junit_row = _junit_rows(root / JUNIT_FILE, frozen_ids, conflicts) if files.get(JUNIT_FILE) else {"cases": {}, "all_pass": False}
    flow_row = _flow_row(root / FLOW_REPORT_FILE, frozen_ids, conflicts) if files.get(FLOW_REPORT_FILE) else {"cases": [], "read_case_absent": None}
    summary_row = _summary_row(root / SUMMARY_FILE, conflicts) if files.get(SUMMARY_FILE) else {}
    run_id = summary_row.get("run_id") or flow_row.get("run_id")
    if run_id and summary_row.get("run_id") and flow_row.get("run_id") \
            and summary_row["run_id"] != flow_row["run_id"]:
        conflicts.append("wpc flow and summary run ids disagree")
    selected_read = summary_row.get("selected_read_case_ids") or []
    if selected_read:
        conflicts.append("wpc local round unexpectedly carries the frozen read case")
    if flow_row.get("read_case_absent") is False:
        conflicts.append("wpc flow report unexpectedly carries the frozen read case")
    return {
        "availability": "ATTACHED",
        "round_label": run_id,
        "wpc_dir_candidates": list(WPC_ROUND_DIR_CANDIDATES),
        "suite": SOURCE_FAMILY_SUITE,
        "files": files,
        "junit": junit_row,
        "flow_case_rows": flow_row.get("cases", []),
        "flow_suite": {
            "run_id": flow_row.get("run_id"),
            "suite_status": flow_row.get("suite_status"),
            "suite": flow_row.get("suite"),
        },
        "summary": summary_row,
        "read_slice_disposition": "LEG_ABSENT",
        "read_slice_basis": (
            "the frozen read slice runs through dual-edge-read-run (r5 read "
            "round and r7 friend-connections state); it is intentionally absent "
            "from this owned-local mutation round, so its local verification "
            "rides the frozen read family envelope instead"),
        "basis": (
            "recorded owned-local round re-read offline by file digest and "
            "content; this arm does not re-execute any transport"),
        "verified": not conflicts,
        "conflicts": conflicts,
    }


# --------------------------------------------------------------------------- #
# arm rendering
# --------------------------------------------------------------------------- #

def density_local_arm_receipt(case_key: str,
                              evidence_root: Path | None = None) -> dict[str, Any]:
    """Render one density owned-local arm, fail-honest."""
    if case_key not in DENSITY_CASE_KEYS:
        raise RunStateError("unknown edge_permission density case key: " + str(case_key))
    row = density_case_row(case_key)
    leaves = tuple(item for item in build_density_obligations()
                   if item.case_key == case_key)
    conflicts: list[str] = []
    binding = frozen_binding_projection(case_key)
    conflicts.extend(binding.pop("conflicts"))
    presence = seam_presence_projection(build_density_obligations())
    if not presence["verified"]:
        conflicts.append("owned-local seam presence drifted from the frozen family")
    literal_rows = seam_literal_rows(leaves)
    for item in literal_rows:
        if item["posture"] != "SOURCE_LITERAL" and item["posture"] != "FOLDED_STRING_CONSTANTS":
            conflicts.append(
                "seam literal drift for " + item["obligation_id"] + ": "
                + str(len(item["missing_literals"])) + " literal(s) left the owner class")
    recorded = recorded_owned_local_round(evidence_root)
    conflicts.extend(recorded.get("conflicts") or [])
    status = LOCAL_MODEL_READY if not conflicts else LOCAL_MODEL_CONFLICT
    return {
        "arm": "owned-local",
        "case_key": case_key,
        "case_id": row["case_id"],
        "public_case_id": row.get("public_case_id"),
        "parent_id": row["parent_id"],
        "status": status,
        "frozen_binding": binding,
        "seam_presence": presence,
        "seam_literal_rows": literal_rows,
        "recorded_owned_local_round": recorded,
        "conflicts": conflicts,
        "leaves_in_arm": len(leaves),
        "basis": (
            "density obligations refine the frozen executed obligations; this "
            "arm never re-executes the owned-local journey and never opens a "
            "transport; contradictions land in the conflict ledger"),
        "credential_kind": "offline_projection_no_transport",
    }


def density_local_arms(evidence_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """Render all five density owned-local arms."""
    return {case_key: density_local_arm_receipt(case_key, evidence_root)
            for case_key in DENSITY_CASE_KEYS}
