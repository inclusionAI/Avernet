"""Source-anchored density obligations for executor row #40 draft group launch.

Wave E2b registers the 75 weighted in-script assertion sites of the frozen
3-bot author/driver/reviewer collaboration Draft-group launch story as
DGL.F01..DGL.F75 obligation leaves under the 17 retained LOCAL-DRAFT-GROUP.*
parent requirements of draft-group-migration-contract.json (92 parsed = 17
parents + 75 atoms; residual_count_gap=0).

Caliber: the owned-local executor freezes the story's machine-readable AST
assertion-site catalog (local_draft_group_contract.ASSERTION_IDS, 75 weighted
sites: one http_status slot per public request/browser call plus cache_control
and envelope_code slots on envelope-shaped sites plus an error_code slot on
frozen error literals, and one slot per check/assert predicate).  This module
rebuilds that catalog independently from the frozen disk bytes, captures each
site's verbatim statement span, and fails closed unless the ordered rebuild
equals the executor's frozen catalog verbatim, so no density leaf can drift
from the evidence the owned-local run publishes.

The story parser declares no optional arguments; the three required story
arguments are derived from the frozen AST main block and fail closed unless
they match the reviewed face exactly.  Parent requirements align to the
committed migration contract: the JSON is live-read and structurally
validated, the executor's own source binding (including the 2174-byte
incident fixture) is re-run, and every parent quote is runtime-extracted from
the frozen story span.  Authorization scheme literals live only inside
machine-extracted quotes (verbatim disk bytes); this source file carries
none.  SHA-256 drift of the story, the incident fixture, or the
Provider/Judge mock fails the build before any projection is usable.
"""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..dynamic_family import dynamic_family_contract
from ..local_draft_group_contract import (
    ASSERTION_IDS, MARKER_KEYS, MARKER_PATTERNS, PARENT, REQUIREMENTS,
    REQUIRED_ASSERTION_COUNT, ROOT, STATUS, STORY_SOURCE, STORY_MARKERS,
    FIXTURE_SOURCE, PROVIDER_JUDGE_MOCK, SUITE, CASE_ID,
    draft_group_source_binding)
from ..run_state import RunStateError

SCHEMA_VERSION = 1
DENSITY_CONTRACT = "draft-group-density-v1"
FAMILY = "draft_group"
DUAL_COMMAND = "dual-draft-group-run"
EXECUTOR_ROW_INDEX = 40
COVERAGE_ROW_NO = 26
SOURCE_FILE = "apps/bcs/scripts/e2e-test/collaboration_draft_group_story.py"
SOURCE_SHA256 = "f0df15f704b9fec3586ece7b76cf7f56c887c2d5fd0fe30fced7e9b58fc28523"
STORY_LINE_SPAN = (17, 231)
COMPANION_SOURCES = {
    "indentation-regression.json": "23e0a22ea7f14f632b1af54a34aaab5bb6679f48580cd99240d36dd6943569e5",
    "http_provider_judge_mock.py": "fe56c8c2d79ce141fe1d2784cc21c184c90000bb8e6908c976168f3bdfad70c4",
}
MIGRATION_CONTRACT = "draft-group-migration-contract.json"
PARENT_OBLIGATION_COUNT = 17
DENSITY_OBLIGATION_COUNT = 75
PARSED_OBLIGATION_COUNT = 92
RESIDUAL_COUNT_GAP = 0
SITE_PREFIX = "LOCAL-DRAFT-GROUP"
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"
DERIVED_STORY_ARGUMENTS = ("--base-url", "--human", "--mock-sink-url")
HELPER_FNS = frozenset({"request", "post", "browser", "cli"})
PUBLIC_HELPERS = frozenset({"request", "post", "browser", "cli", "check"})
WEIGHTED = True
EXPECTED_WEBHOOKS = 0
WEIGHTED_SLOT_RULE = (
    "http_status slot per public request/browser site; cache_control and "
    "envelope_code slots on browser-shaped sites and on request sites with a "
    "frozen envelope=True constant; error_code slot when the error keyword is "
    "a frozen string constant; one predicate slot per check/assert site"
)

# Stage spans whose lines perform no machine-readable check site; their
# enforcement evidence is observed by the downstream stage atoms.
ZERO_ATOM_PARENTS = {
    "LOCAL-DRAFT-GROUP.group-launch-input": (
        "story lines 162-172 only build the in-memory launch body dict (edited "
        "YAML, group kind, originator, state-machine strategy, content and "
        "participant bindings); no public call or check site exists on those "
        "lines, so the stage stays a parent obligation and its enforcement is "
        "observed by the browser-context-submission-guards atoms "
        "DGL.F49..DGL.F51 and the group-launch-binding atoms DGL.F56..DGL.F64"),
}
NOT_CHARGED_PROBES = (
    "the story performs no dynamic wait predicates; the single terminal marker "
    "print is the story marker observation, and the executor's frozen weighted "
    "AST catalog charges one http_status slot per public call site plus the "
    "envelope and error slots declared in the weighted slot rule"
)


@dataclass(frozen=True)
class Obligation:
    """One parent stage or in-script assertion leaf with a verbatim anchor."""

    obligation_id: str
    level: str
    method: str
    line_start: int
    line_end: int
    quote: str
    parent_obligation: str
    expectation: str
    operation: str
    field: str
    comparator: str
    expected: Any = None
    executor_site_id: str | None = None
    slot: str | None = None
    story_label: str | None = None
    http_method: str | None = None
    http_path: str | None = None

    @property
    def source_file(self) -> str:
        return SOURCE_FILE

    @property
    def source_line(self) -> int:
        return self.line_start

    @property
    def source_quote(self) -> str:
        return self.quote

    @property
    def located(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.obligation_id, "level": self.level, "method": self.method,
            "line_start": self.line_start, "line_end": self.line_end,
            "quote": self.quote, "source_file": SOURCE_FILE,
            "parent_obligation": self.parent_obligation,
            "expectation": self.expectation, "operation": self.operation,
            "field": self.field, "comparator": self.comparator,
            "expected": self.expected, "located": True,
            "executor_site_id": self.executor_site_id, "slot": self.slot,
            "story_label": self.story_label, "http_method": self.http_method,
            "http_path": self.http_path}


COMPANION_PATHS = {
    "indentation-regression.json": FIXTURE_SOURCE,
    "http_provider_judge_mock.py": PROVIDER_JUDGE_MOCK,
}


def _source_paths() -> dict[str, Path]:
    # Built per call so tests can patch the module path constants.
    return {"collaboration_draft_group_story.py": STORY_SOURCE,
            **COMPANION_PATHS}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _constant(node: ast.expr | None) -> Any:
    return node.value if isinstance(node, ast.Constant) else None


def _derive_required_story_arguments(text: str) -> tuple[str, ...]:
    """Derive the frozen parser face from the story main block AST."""
    derived: list[str] = []
    for node in ast.walk(ast.parse(text, filename=SOURCE_FILE)):
        if (isinstance(node, ast.Call)
                and getattr(node.func, "attr", "") == "add_argument"):
            name = _constant(node.args[0]) if node.args else None
            required = any(keyword.arg == "required"
                           and _constant(keyword.value) is True
                           for keyword in node.keywords)
            if isinstance(name, str) and name.startswith("--") and required:
                derived.append(name)
    if tuple(derived) != DERIVED_STORY_ARGUMENTS:
        raise RunStateError("draft-group story argument face changed")
    return tuple(derived)


def _slot_plan(kind: str, keywords: dict[str, ast.expr]) -> list[tuple[str, ast.expr | None]]:
    if kind in {"assert", "check"}:
        return [("predicate", None)]
    slots: list[tuple[str, ast.expr | None]] = [("http_status", keywords.get("status"))]
    envelope = keywords.get("envelope")
    if kind == "browser" or (isinstance(envelope, ast.Constant) and envelope.value is True):
        slots.append(("cache_control", None))
        slots.append(("envelope_code", None))
    error = keywords.get("error")
    if isinstance(error, ast.Constant) and error.value is not None:
        slots.append(("error_code", error))
    return slots


def _status_expectation(node: ast.expr | None, site_id: str) -> tuple[str, Any]:
    if node is None:
        return "equals", 200
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return "equals", node.value
    if isinstance(node, ast.Tuple):
        values = [item.value for item in node.elts
                  if isinstance(item, ast.Constant) and isinstance(item.value, int)]
        if values and len(values) == len(node.elts):
            return "in", tuple(values)
    if isinstance(node, ast.IfExp):
        arms = []
        for arm in (node.body, node.orelse):
            if isinstance(arm, ast.Constant) and isinstance(arm.value, int):
                arms.append(arm.value)
        if len(arms) == 2:
            return "in", tuple(sorted(arms))
    raise RunStateError("draft-group status expectation is not frozen: " + site_id)


def _site_nodes(text: str) -> list[dict[str, Any]]:
    """Walk the story in the executor catalog's exact order with spans."""
    rows: list[dict[str, Any]] = []

    class _Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.fn = "module"

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            outer, self.fn = self.fn, node.name
            self.generic_visit(node)
            self.fn = outer

        def visit_Assert(self, node: ast.Assert) -> None:
            if self.fn not in HELPER_FNS:
                rows.append({"kind": "assert", "fn": self.fn, "node": node,
                             "label": _constant(node.msg)})
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            name = getattr(node.func, "attr", "")
            if name in PUBLIC_HELPERS and self.fn not in HELPER_FNS:
                keywords = {item.arg: item.value for item in node.keywords}
                if name == "check":
                    label = _constant(node.args[1]) if len(node.args) > 1 else None
                else:
                    label = _constant(node.args[0]) if node.args else None
                if name in {"request", "browser"}:
                    method = _constant(node.args[1]) if len(node.args) > 1 else None
                    path = node.args[2] if len(node.args) > 2 else None
                elif name == "post":
                    method, path = "POST", node.args[0] if node.args else None
                else:
                    method, path = None, None
                rows.append({"kind": name, "fn": self.fn, "node": node,
                             "label": label, "http_method": method,
                             "http_path": ast.get_source_segment(text, path)
                             if path is not None else None,
                             "keywords": keywords})
            self.generic_visit(node)

    _Visitor().visit(ast.parse(text, filename=SOURCE_FILE))
    return rows


def _parent_contract_value() -> dict:
    path = ROOT / MIGRATION_CONTRACT
    value = json.loads(path.read_text(encoding="utf-8"))
    if (set(value) != {"schema_version", "source_revision", "source_file_revisions",
                       "parent", "suite", "case_id", "environment", "sources",
                       "definition_yaml_bytes", "owned_runtime_binary", "mock_child",
                       "requirements", "story_markers", "remote_validation",
                       "grants_release_credit", "release_parent_denominator",
                       "independent_slice", "evidence_granularity"}
            or value["parent"] != PARENT or value["suite"] != SUITE
            or value["case_id"] != CASE_ID
            or value["environment"] != {"local": "LOCAL_EXECUTOR_ONLY",
                                        "preprod": "REMOTE_UNSUPPORTED"}
            or value["story_markers"] != list(STORY_MARKERS)
            or value["definition_yaml_bytes"] != 2174
            or value["remote_validation"] != "NOT_RUN"
            or value["grants_release_credit"] is not False
            or value["release_parent_denominator"] is not False
            or value["independent_slice"] is not True
            or value["evidence_granularity"] != "OWNED_CHILD_RECEIPT"
            or value["mock_child"] != {"script": "http_provider_judge_mock.py",
                                       "ready_protocol": "ready-file",
                                       "expected_webhook_requests": EXPECTED_WEBHOOKS}):
        raise RunStateError("draft-group migration contract face changed")
    if [row["id"] for row in value["requirements"]] != list(REQUIREMENTS):
        raise RunStateError("draft-group parent requirement order changed")
    return value


def _density_expectation(slot: str, site_id: str, entry: dict[str, Any],
                         status_pair: tuple[str, Any],
                         comparator: str, expected: Any) -> str:
    label = entry.get("label")
    at = f" story step '{label}'" if label else ""
    if slot == "predicate":
        base = "In-script predicate check holds at this site"
        return base + (at + "." if at else ".")
    method = entry.get("http_method") or "HTTP"
    path = (entry.get("http_path") or "")[:110]
    if slot == "http_status":
        if comparator == "in":
            return (f"HTTP {method} {path} returns one of the accepted statuses "
                    f"{list(expected)}{at}.")
        return f"HTTP {method} {path} returns status {expected}{at}."
    if slot == "cache_control":
        return (f"HTTP {method} {path} response carries Cache-Control no-store "
                f"(envelope sites are never cached){at}.")
    if slot == "envelope_code":
        return f"HTTP {method} {path} response envelope code equals {expected} (status * 100){at}."
    if slot == "error_code":
        return f"HTTP {method} {path} response envelope error_code equals {expected}{at}."
    raise RunStateError("draft-group density slot is not frozen: " + site_id)


def _build_density_rows(text: str, lines: list[str],
                        owner_map: dict[str, tuple[str, ...]]) -> list[Obligation]:
    rows: list[Obligation] = []
    built_ids: list[str] = []
    cursors: dict[tuple[str, str, int], int] = {}
    for entry in _site_nodes(text):
        node, kind, fn = entry["node"], entry["kind"], entry["fn"]
        slots = _slot_plan(kind, entry.get("keywords", {}))
        group = (fn, kind, node.lineno)
        span = (node.lineno, node.end_lineno)
        quote = "\n".join(lines[span[0] - 1:span[1]])
        status_pair = _status_expectation(
            entry.get("keywords", {}).get("status") if kind not in {"assert", "check"}
            else None,
            f"{SITE_PREFIX}.{fn}.{kind}-{node.lineno:03d}")
        for slot_name, slot_node in slots:
            index = cursors.get(group, 0) + 1
            cursors[group] = index
            site_id = f"{SITE_PREFIX}.{fn}.{kind}-{node.lineno:03d}-{index:02d}"
            built_ids.append(site_id)
            owners = owner_map.get(site_id, ())
            if len(owners) != 1 or owners[0] not in REQUIREMENTS:
                raise RunStateError("draft-group site owner is not one frozen parent: "
                                    + site_id)
            if slot_name == "predicate":
                field, comparator, expected = "predicate", "predicate", True
                method = "story_assert" if kind == "assert" else "story_check"
            elif slot_name == "http_status":
                field, comparator, expected = "http_status", status_pair[0], status_pair[1]
                method = f"http_{str(entry.get('http_method') or 'request').lower()}"
            elif slot_name == "cache_control":
                field, comparator, expected = "cache_control", "equals", "no-store"
                method = f"http_{str(entry.get('http_method') or 'request').lower()}"
            elif slot_name == "envelope_code":
                field = "envelope_code"
                comparator, expected = "equals", int(status_pair[1]) * 100
                method = f"http_{str(entry.get('http_method') or 'request').lower()}"
            else:
                field, comparator, expected = "error_code", "equals", _constant(slot_node)
                method = f"http_{str(entry.get('http_method') or 'request').lower()}"
            leaf_id = f"DGL.F{len(rows) + 1:02d}"
            rows.append(Obligation(
                obligation_id=leaf_id, level="DENSITY", method=method,
                line_start=span[0], line_end=span[1], quote=quote,
                parent_obligation=owners[0],
                expectation=_density_expectation(
                    slot_name, site_id, entry, status_pair, comparator, expected),
                operation=owners[0], field=field, comparator=comparator,
                expected=list(expected) if isinstance(expected, tuple) else expected,
                executor_site_id=site_id, slot=slot_name,
                story_label=entry.get("label"),
                http_method=entry.get("http_method"),
                http_path=entry.get("http_path")))
    if tuple(built_ids) != tuple(ASSERTION_IDS):
        raise RunStateError("draft-group assertion-site catalog changed")
    return rows


def _validate_source(text: str) -> None:
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != SOURCE_SHA256:
        raise RunStateError("draft-group density source SHA-256 drifted")
    for name, digest in COMPANION_SOURCES.items():
        path = _source_paths()[name]
        if (path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise RunStateError("draft-group companion source drifted: " + name)


def obligations() -> tuple[Obligation, ...]:
    """Freeze, validate and return the 92-row source-anchored plan."""
    draft_group_source_binding()
    contract_value = _parent_contract_value()
    paths = _source_paths()
    story_path = paths["collaboration_draft_group_story.py"]
    if story_path.is_symlink() or not story_path.is_file():
        raise RunStateError("draft-group density source is unavailable")
    raw = story_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RunStateError("draft-group density source SHA-256 drifted")
    text = raw.decode("utf-8")
    _validate_source(text)
    lines = text.splitlines()
    contract_sources = dict(contract_value["sources"])
    if contract_sources.get("collaboration_draft_group_story.py") != SOURCE_SHA256:
        raise RunStateError("draft-group density digest disagrees with the contract")
    for name, digest in COMPANION_SOURCES.items():
        if contract_sources.get(name) != digest:
            raise RunStateError("draft-group companion digest disagrees with the contract")
    for name, digest in contract_value["source_file_revisions"].items():
        if name not in paths or not isinstance(digest, str) or len(digest) != 40:
            raise RunStateError("draft-group contract frozen revision map changed")
    _derive_required_story_arguments(text)
    family = dynamic_family_contract(FAMILY)
    if family.assertion_ids != tuple(ASSERTION_IDS):
        raise RunStateError("draft-group executor catalog disagrees with the family map")
    if family.requirement_ids != tuple(REQUIREMENTS):
        raise RunStateError("draft-group parent requirements disagree with the family map")
    parents = []
    for requirement in contract_value["requirements"]:
        start, end = requirement["story_lines"]
        if not (1 <= start <= end <= len(lines)) or list(requirement["markers"]) != list(
                MARKER_KEYS):
            raise RunStateError("draft-group parent stage anchor invalid: "
                                + requirement["id"])
        parents.append(Obligation(
            obligation_id=requirement["id"], level="PARENT", method="story_stage",
            line_start=start, line_end=end, quote="\n".join(lines[start - 1:end]),
            parent_obligation=requirement["id"], expectation=requirement["expectation"],
            operation=requirement["id"], field="stage", comparator="stage",
            expected=None, executor_site_id=None, slot="stage", story_label=None,
            http_method=None, http_path=None))
    leaves = _build_density_rows(text, lines, family.assertion_requirement_ids)
    rows = tuple(parents) + tuple(leaves)
    if (len(parents) != PARENT_OBLIGATION_COUNT or len(leaves) != DENSITY_OBLIGATION_COUNT
            or len(rows) != PARSED_OBLIGATION_COUNT):
        raise RunStateError("draft-group density layer count changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("draft-group density obligation IDs are not unique")
    story_lines = {row["id"]: row["story_lines"] for row in contract_value["requirements"]}
    for leaf in leaves:
        span = story_lines[leaf.parent_obligation]
        if not (span[0] <= leaf.line_start <= leaf.line_end <= span[1]):
            raise RunStateError("draft-group density leaf escapes its parent span: "
                                + leaf.obligation_id)
        if leaf.quote != "\n".join(lines[leaf.line_start - 1:leaf.line_end]):
            raise RunStateError("draft-group density anchor drifted: " + leaf.obligation_id)
    marked_parents = {parent.obligation_id for parent in parents}
    charged = {leaf.parent_obligation for leaf in leaves}
    if charged - marked_parents or set(story_lines) != marked_parents:
        raise RunStateError("draft-group parent envelope changed")
    if {parent for parent in marked_parents if parent not in charged} != set(
            ZERO_ATOM_PARENTS):
        raise RunStateError("draft-group zero-atom parent disclosure changed")
    return rows


def density_plan() -> dict[str, Any]:
    rows = obligations()
    family = dynamic_family_contract(FAMILY)
    story_text = STORY_SOURCE.read_text(encoding="utf-8")
    return {
        "schema_version": SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "parent": PARENT,
        "executor_row_index": EXECUTOR_ROW_INDEX,
        "coverage_row_no": COVERAGE_ROW_NO,
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_SHA256,
        "story_line_span": list(STORY_LINE_SPAN),
        "companion_sources": dict(COMPANION_SOURCES),
        "migration_contract": MIGRATION_CONTRACT,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "weighted": WEIGHTED,
        "weighted_slot_rule": WEIGHTED_SLOT_RULE,
        "projection_rule": (
            "17 frozen LOCAL-DRAFT-GROUP.* contract parents plus 75 weighted verbatim "
            "statement anchors rebuilt from the story AST in the owned-local executor's "
            "frozen catalog order (ordered identity DGL catalog 75/75); the parent "
            "envelope absorbs the terminal marker print, and "
            "LOCAL-DRAFT-GROUP.group-launch-input is the single disclosed zero-atom "
            "parent because its story lines only build the launch body dict"),
        "obligations": [row.as_dict() for row in rows],
        "parent_ids": [row.obligation_id for row in rows if row.level == "PARENT"],
        "leaf_ids": [row.obligation_id for row in rows if row.level == "DENSITY"],
        "parsed_unlocated_count": 0,
        "parsed_unlocated": [],
        "not_charged_source_probes": [NOT_CHARGED_PROBES],
        "zero_atom_parents": dict(ZERO_ATOM_PARENTS),
        "per_parent_atom_counts": {
            parent: len(family.sites_for(parent)) for parent in REQUIREMENTS},
        "executor_catalog": {
            "source": "bcs_e2e.local_draft_group_contract.ASSERTION_IDS",
            "count": REQUIRED_ASSERTION_COUNT,
            "ordered_identity": True,
            "weighted": True,
            "case_id": CASE_ID,
            "suite": SUITE,
            "local_status": STATUS,
            "local_executor": family.local_executor,
            "dual_command": DUAL_COMMAND,
            "source_case_id": family.source_case_id,
            "source_suite": family.source_suite,
        },
        "story_arguments": {
            "required": list(DERIVED_STORY_ARGUMENTS),
            "optional": [],
            "derivation": "AST main-block parser add_argument(required=True) rebuild",
            "contract_face": "absent in draft-group-migration-contract.json by design"},
        "story_marker": {"keys": list(MARKER_KEYS), "patterns": list(MARKER_PATTERNS),
                         "marker": STORY_MARKERS[0]},
        "mock_child": {"script": "http_provider_judge_mock.py",
                       "ready_protocol": "ready-file",
                       "expected_webhook_requests": EXPECTED_WEBHOOKS},
        "named_preprod_gaps": list(family.preprod_gaps),
        "source_read_only": True,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV],
    }
