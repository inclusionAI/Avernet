"""Source-anchored density obligations for executor row #20 result queue.

Wave E2b registers the 34 in-script assertion sites of the frozen one-shot
result-queue story as RQ.F01..RQ.F34 obligation leaves under the 18 retained
RESULT-QUEUE.* parent requirements of result-queue-migration-contract.json
(52 parsed = 18 parents + 34 atoms; residual_count_gap=0).

Caliber: the owned-local executor already freezes the story's machine-readable
AST assertion-site catalog (local_result_queue_contract.ASSERTION_IDS, 34
sites: one per public request/post call site plus one per Python assert).
This module rebuilds that catalog independently from the frozen disk bytes,
captures each site's verbatim statement span, and fails closed unless the
ordered rebuild equals the executor's frozen catalog verbatim, so no density
leaf can drift from the evidence the owned-local run publishes.

Parent requirements align to the committed migration contract: the JSON is
live-read and structurally validated, the executor's own source binding is
re-run, and every parent quote is runtime-extracted from the frozen story
span.  Authorization scheme literals live only inside machine-extracted
quotes (verbatim disk bytes); this source file carries none.  SHA-256 drift
of the story, its fixed-Loop base, or the Provider/Judge mock fails the
build before any projection is usable.
"""
from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..dynamic_family import dynamic_family_contract
from ..local_result_queue_contract import (
    ASSERTION_IDS, MARKER_KEYS, MARKER_PATTERNS, PARENT, REQUIREMENTS,
    REQUIRED_ASSERTION_COUNT, REQUIRED_STORY_ARGUMENTS, ROOT, STATUS,
    STORY_SOURCE, STORY_MARKERS, BASE_SOURCE, PROVIDER_JUDGE_MOCK, SUITE,
    CASE_ID, result_queue_source_binding)
from ..run_state import RunStateError

SCHEMA_VERSION = 1
DENSITY_CONTRACT = "result-queue-density-v1"
FAMILY = "result_queue"
DUAL_COMMAND = "dual-result-queue-run"
EXECUTOR_ROW_INDEX = 20
COVERAGE_ROW_NO = 25
SOURCE_FILE = "apps/bcs/scripts/e2e-test/result_queue_story.py"
SOURCE_SHA256 = "b672cc7bc0543686426159d8a694389fe621f9c1cbf9557ee1ae1f1cd41d1dca"
STORY_LINE_SPAN = (14, 140)
COMPANION_SOURCES = {
    "fixed_loop_story.py": "8fc6daeace717e1e030478c398ed1a770df192c69fcd7622cd34cbdca11d17d5",
    "http_provider_judge_mock.py": "fe56c8c2d79ce141fe1d2784cc21c184c90000bb8e6908c976168f3bdfad70c4",
}
MIGRATION_CONTRACT = "result-queue-migration-contract.json"
PARENT_OBLIGATION_COUNT = 18
DENSITY_OBLIGATION_COUNT = 34
PARSED_OBLIGATION_COUNT = 52
RESIDUAL_COUNT_GAP = 0
SITE_PREFIX = "RESULT-QUEUE"
HUMAN_COOKIE_ENV = "BCS_E2E_HUMAN_COOKIE"
DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"
HELPER_FNS = frozenset({"request", "post", "browser", "cli"})
PUBLIC_HELPERS = frozenset({"request", "post", "browser", "cli", "check"})
WEIGHTED = False
EXPECTED_WEBHOOKS = 3

# Stage spans whose lines perform no machine-readable check site; their
# enforcement evidence is observed by the downstream stage atoms.
ZERO_ATOM_PARENTS = {
    "RESULT-QUEUE.delivery-policy-replacement": (
        "story lines 41-46 only mutate the in-memory policy dict (flow enablement and "
        "enforce ceilings); no public call or assert site exists on those lines, so the "
        "stage stays a parent obligation and its enforcement is observed by the "
        "scheduled-result-delivery atoms RQ.F21..RQ.F23"),
}
NOT_CHARGED_PROBES = (
    "the story's seven dynamic wait predicates (Provider callback acceptance, "
    "one-shot node delivery, durable result admission, scheduled result delivery, "
    "completed result delivery, observer bound context, consumed context) are "
    "stage-level evidence carried by their owning RESULT-QUEUE.* parents, and the "
    "single terminal marker print is the story marker observation; the executor's "
    "frozen AST catalog charges one site per public call or assert statement"
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
            "http_path": self.http_path,
        }


def _source_paths() -> dict[str, Path]:
    # Built per call so tests can patch the module path constants.
    return {"result_queue_story.py": STORY_SOURCE, **COMPANION_PATHS}


COMPANION_PATHS = {
    "fixed_loop_story.py": BASE_SOURCE,
    "http_provider_judge_mock.py": PROVIDER_JUDGE_MOCK,
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _constant(node: ast.expr | None) -> Any:
    return node.value if isinstance(node, ast.Constant) else None


def _slot_plan(kind: str, keywords: dict[str, ast.expr]) -> list[tuple[str, ast.expr | None]]:
    if kind in {"assert", "check"}:
        return [("predicate", None)]
    return [("http_status", keywords.get("status"))]


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
    raise RunStateError("result-queue status expectation is not frozen: " + site_id)


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
                if name == "post":
                    method, path = "POST", node.args[0] if node.args else None
                else:
                    method = _constant(keywords.get("method")) or (
                        "POST" if name == "post" else "GET")
                    path = node.args[2] if len(node.args) > 2 else None
                rows.append({"kind": name, "fn": self.fn, "node": node,
                             "label": _constant(node.args[0]) if node.args else None,
                             "http_method": method,
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
                       "required_story_arguments", "source_marker_lines",
                       "owned_runtime_binary", "mock_child", "requirements",
                       "story_markers", "remote_validation", "grants_release_credit",
                       "release_parent_denominator", "independent_slice",
                       "evidence_granularity"}
            or value["parent"] != PARENT or value["suite"] != SUITE
            or value["case_id"] != CASE_ID
            or value["environment"] != {"local": "LOCAL_EXECUTOR_ONLY",
                                        "preprod": "REMOTE_UNSUPPORTED"}
            or list(value["required_story_arguments"]) != list(REQUIRED_STORY_ARGUMENTS)
            or value["source_marker_lines"] != 1
            or value["story_markers"] != list(STORY_MARKERS)
            or value["remote_validation"] != "NOT_RUN"
            or value["grants_release_credit"] is not False
            or value["release_parent_denominator"] is not False
            or value["independent_slice"] is not True
            or value["evidence_granularity"] != "OWNED_CHILD_RECEIPT"
            or value["mock_child"] != {"script": "http_provider_judge_mock.py",
                                       "ready_protocol": "ready-file",
                                       "expected_webhook_requests": EXPECTED_WEBHOOKS}):
        raise RunStateError("result-queue migration contract face changed")
    if [row["id"] for row in value["requirements"]] != list(REQUIREMENTS):
        raise RunStateError("result-queue parent requirement order changed")
    return value


def _density_expectation(slot: str, site_id: str, entry: dict[str, Any],
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
    raise RunStateError("result-queue density slot is not frozen: " + site_id)


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
        for slot_name, status_node in slots:
            index = cursors.get(group, 0) + 1
            cursors[group] = index
            site_id = f"{SITE_PREFIX}.{fn}.{kind}-{node.lineno:03d}-{index:02d}"
            built_ids.append(site_id)
            owners = owner_map.get(site_id, ())
            if len(owners) != 1 or owners[0] not in REQUIREMENTS:
                raise RunStateError("result-queue site owner is not one frozen parent: "
                                    + site_id)
            if slot_name == "predicate":
                field, comparator, expected = "predicate", "predicate", True
                method = "story_assert" if kind == "assert" else "story_check"
            else:
                field = "http_status"
                comparator, expected = _status_expectation(status_node, site_id)
                method = f"http_{str(entry.get('http_method') or 'request').lower()}"
            leaf_id = f"RQ.F{len(rows) + 1:02d}"
            rows.append(Obligation(
                obligation_id=leaf_id, level="DENSITY", method=method,
                line_start=span[0], line_end=span[1], quote=quote,
                parent_obligation=owners[0],
                expectation=_density_expectation(slot_name, site_id, entry,
                                                  comparator, expected),
                operation=owners[0], field=field, comparator=comparator,
                expected=list(expected) if isinstance(expected, tuple) else expected,
                executor_site_id=site_id, slot=slot_name,
                story_label=entry.get("label"),
                http_method=entry.get("http_method"),
                http_path=entry.get("http_path")))
    if tuple(built_ids) != tuple(ASSERTION_IDS):
        raise RunStateError("result-queue assertion-site catalog changed")
    return rows


def _validate_source(text: str) -> None:
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != SOURCE_SHA256:
        raise RunStateError("result-queue density source SHA-256 drifted")
    for name, digest in COMPANION_SOURCES.items():
        path = _source_paths()[name]
        if (path.is_symlink() or not path.is_file()
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise RunStateError("result-queue companion source drifted: " + name)


def obligations() -> tuple[Obligation, ...]:
    """Freeze, validate and return the 52-row source-anchored plan."""
    result_queue_source_binding()
    contract_value = _parent_contract_value()
    paths = _source_paths()
    story_path = paths["result_queue_story.py"]
    if story_path.is_symlink() or not story_path.is_file():
        raise RunStateError("result-queue density source is unavailable")
    raw = story_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise RunStateError("result-queue density source SHA-256 drifted")
    text = raw.decode("utf-8")
    _validate_source(text)
    lines = text.splitlines()
    contract_sources = dict(contract_value["sources"])
    if contract_sources.get("result_queue_story.py") != SOURCE_SHA256:
        raise RunStateError("result-queue density digest disagrees with the contract")
    for name, digest in COMPANION_SOURCES.items():
        if contract_sources.get(name) != digest:
            raise RunStateError("result-queue companion digest disagrees with the contract")
    for name, digest in contract_value["source_file_revisions"].items():
        if name not in paths or not isinstance(digest, str) or len(digest) != 40:
            raise RunStateError("result-queue contract frozen revision map changed")
    family = dynamic_family_contract(FAMILY)
    if family.assertion_ids != tuple(ASSERTION_IDS):
        raise RunStateError("result-queue executor catalog disagrees with the family map")
    if family.requirement_ids != tuple(REQUIREMENTS):
        raise RunStateError("result-queue parent requirements disagree with the family map")
    parents = []
    for requirement in contract_value["requirements"]:
        start, end = requirement["story_lines"]
        if not (1 <= start <= end <= len(lines)) or list(requirement["markers"]) != list(
                MARKER_KEYS):
            raise RunStateError("result-queue parent stage anchor invalid: "
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
        raise RunStateError("result-queue density layer count changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("result-queue density obligation IDs are not unique")
    story_lines = {row["id"]: row["story_lines"] for row in contract_value["requirements"]}
    for leaf in leaves:
        span = story_lines[leaf.parent_obligation]
        if not (span[0] <= leaf.line_start <= leaf.line_end <= span[1]):
            raise RunStateError("result-queue density leaf escapes its parent span: "
                                + leaf.obligation_id)
        if leaf.quote != "\n".join(lines[leaf.line_start - 1:leaf.line_end]):
            raise RunStateError("result-queue density anchor drifted: " + leaf.obligation_id)
    marked_parents = {parent.obligation_id for parent in parents}
    charged = {leaf.parent_obligation for leaf in leaves}
    if charged - marked_parents or set(story_lines) != marked_parents:
        raise RunStateError("result-queue parent envelope changed")
    if {parent for parent in marked_parents if parent not in charged} != set(
            ZERO_ATOM_PARENTS):
        raise RunStateError("result-queue zero-atom parent disclosure changed")
    return rows


def density_plan() -> dict[str, Any]:
    rows = obligations()
    family = dynamic_family_contract(FAMILY)
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
        "projection_rule": (
            "18 frozen RESULT-QUEUE.* contract parents plus 34 verbatim statement "
            "anchors rebuilt from the story AST in the owned-local executor's frozen "
            "catalog order (ordered identity RQ catalog 34/34); the parent envelope "
            "absorbs the seven dynamic wait predicates and the terminal marker print, "
            "and RESULT-QUEUE.delivery-policy-replacement is the single disclosed "
            "zero-atom parent because its story lines only mutate the policy dict"),
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
            "source": "bcs_e2e.local_result_queue_contract.ASSERTION_IDS",
            "count": REQUIRED_ASSERTION_COUNT,
            "ordered_identity": True,
            "case_id": CASE_ID,
            "suite": SUITE,
            "local_status": STATUS,
            "local_executor": family.local_executor,
            "dual_command": DUAL_COMMAND,
            "source_case_id": family.source_case_id,
            "source_suite": family.source_suite,
        },
        "story_arguments": {"required": list(REQUIRED_STORY_ARGUMENTS),
                            "optional": ["--mock-sink-url"]},
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
