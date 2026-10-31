"""Independent source-anchored rebuild for the operator CLI density wave.

Wave idx9 registers the 15 frozen shell assertion spans of the operator CLI
story as OPCLI.F01..OPCLI.F15 density leaves under the 13 retained
OPERATOR-CLI.* parent requirements of operator-cli-migration-contract.json
(28 parsed = 13 legacy parents + 15 atoms; residual_count_gap=0).

Caliber: this module rebuilds the plan independently from the frozen disk
bytes.  Parent rows quote the exact helper call line inside the
story_operator_coordinates_with_cli function body in stories.sh; every atom
row quotes its verbatim (file, line_start, line_end) span from cli.sh or
cli-stories.sh.  The rebuild fails closed unless the story function span,
the thirteen ordered parent call lines, every atom span containment inside
its owning helper body, every span's single occurrence on disk, and the
ordered leaf/site identity against the frozen literals all hold, so no
density leaf can drift from the shell evidence the owned-local run
publishes.

The committed migration contract is live-read through the executor's own
source binding, the dual-face adapter methods and frozen CLI operations are
re-checked live, and SHA-256 drift of stories.sh or any companion source
fails the build before any projection is usable.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..run_state import RunStateError
from . import contract
from .contract import (
    ADAPTER_METHODS, CATALOG, CLI_OPERATIONS, COMMANDS, COMPANION_SOURCES,
    CONTRACT_REVISION, COVERAGE_ROW_NO, DENSITY_CONTRACT, DUAL_CASE_ID,
    DUAL_COMMAND, DUAL_SUITE, EXECUTOR_ROW_INDEX, FAMILY, HELPERS,
    HELPERS_FILE_MAP, HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV, LOCAL_EXECUTOR,
    LOCAL_STATUS, CANONICAL_STATUS, MIGRATION_CONTRACT, NOT_CHARGED_PROBES,
    PARENT_CALL_LINES, PARENT_OBLIGATION_COUNT, DENSITY_OBLIGATION_COUNT,
    PARSED_OBLIGATION_COUNT, PREPROD_GAPS, PROFILE_CONTRACT, REQUIREMENTS,
    REPORT_CONTRACT, RESIDUAL_COUNT_GAP, SOURCE_FILE, SOURCE_SHA256, STORIES_SH,
    STORY_CALL_SPAN, STORY_LINE_SPAN, SUITE, CASE_ID, ZERO_ATOM_PARENTS,
    _call_lines, _function_span)

ASSERTION_IDS = ("OPCLI.F01", "OPCLI.F02", "OPCLI.F03", "OPCLI.F04",
                 "OPCLI.F05", "OPCLI.F06", "OPCLI.F07", "OPCLI.F08",
                 "OPCLI.F09", "OPCLI.F10", "OPCLI.F11", "OPCLI.F12",
                 "OPCLI.F13", "OPCLI.F14", "OPCLI.F15")
SITE_IDS = ("OPERATOR-CLI.health.payload", "OPERATOR-CLI.list.uuids",
            "OPERATOR-CLI.get.uuid", "OPERATOR-CLI.discover.match",
            "OPERATOR-CLI.visibility.set-public", "OPERATOR-CLI.visibility.restore",
            "OPERATOR-CLI.connect.session", "OPERATOR-CLI.onboard-web.url",
            "OPERATOR-CLI.direct-onboard.persist",
            "OPERATOR-CLI.direct-onboard.lifecycle",
            "OPERATOR-CLI.update-status.status", "OPERATOR-CLI.chat.handle",
            "OPERATOR-CLI.chat.run-status-cancel", "OPERATOR-CLI.list-groups.shape",
            "OPERATOR-CLI.friend-list.uuid")

EXPECTED_LEAF_COUNTS = {
    "OPERATOR-CLI.health": 1,
    "OPERATOR-CLI.list": 1,
    "OPERATOR-CLI.get": 1,
    "OPERATOR-CLI.discover": 1,
    "OPERATOR-CLI.visibility": 2,
    "OPERATOR-CLI.connect": 1,
    "OPERATOR-CLI.onboard-web": 1,
    "OPERATOR-CLI.direct-onboard": 2,
    "OPERATOR-CLI.update-status": 1,
    "OPERATOR-CLI.create-group": 0,
    "OPERATOR-CLI.chat": 2,
    "OPERATOR-CLI.list-groups": 1,
    "OPERATOR-CLI.friend-list": 1,
}


@dataclass(frozen=True)
class Obligation:
    """One parent stage or shell assertion leaf with a verbatim anchor."""

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
    source_name: str = STORIES_SH

    @property
    def source_file(self) -> str:
        return contract.SOURCE_DISPLAY[self.source_name]

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
            "quote": self.quote, "source_file": self.source_file,
            "source_name": self.source_name,
            "parent_obligation": self.parent_obligation,
            "expectation": self.expectation, "operation": self.operation,
            "field": self.field, "comparator": self.comparator,
            "expected": self.expected, "located": True,
            "executor_site_id": self.executor_site_id, "slot": self.slot,
            "story_label": self.story_label, "source_read_only": True,
        }


def _source_paths() -> dict[str, Path]:
    # Re-exported per call so tests can patch the contract path helper.
    return contract._source_paths()


def _read_source(path: Path, name: str, digest: str) -> str:
    if path.is_symlink() or not path.is_file():
        raise RunStateError("operator-cli density source is unavailable: " + name)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RunStateError("operator-cli density source SHA-256 drifted: " + name)
    return raw.decode("utf-8")


def _build_parents(story_text: str, contract_value: dict[str, Any],
                   story_lines: list[str]) -> list[Obligation]:
    span_start, span_end, body = _function_span(story_text, contract.PARENT_STORY)
    if (span_start, span_end) != STORY_LINE_SPAN:
        raise RunStateError("operator-cli story function span changed")
    derived_calls = _call_lines(body, span_start, HELPERS)
    if tuple(derived_calls) != PARENT_CALL_LINES or derived_calls != sorted(
            derived_calls):
        raise RunStateError("operator-cli story call-line span changed")
    if derived_calls[0] != STORY_CALL_SPAN[0] or derived_calls[-1] != STORY_CALL_SPAN[1]:
        raise RunStateError("operator-cli story call lines escaped the frozen span")
    expectations = {row["id"]: row["expectation"]
                    for row in contract_value["requirements"]}
    rows: list[Obligation] = []
    for requirement, call_line in zip(REQUIREMENTS, derived_calls):
        rows.append(Obligation(
            obligation_id=requirement, level="PARENT", method="story_stage",
            line_start=call_line, line_end=call_line,
            quote=story_lines[call_line - 1],
            parent_obligation=requirement, expectation=expectations[requirement],
            operation=requirement, field="stage", comparator="stage",
            expected=None, executor_site_id=None, slot="stage", story_label=None,
            source_name=STORIES_SH))
    return rows


def _build_leaves() -> list[Obligation]:
    paths = _source_paths()
    digests = {STORIES_SH: SOURCE_SHA256, **COMPANION_SOURCES}
    texts: dict[str, str] = {}
    for name in (contract.CLI_SH, contract.CLI_STORIES_SH):
        texts[name] = _read_source(paths[name], name, digests[name])
    rows: list[Obligation] = []
    for spec in CATALOG:
        if spec.source_name not in (contract.CLI_SH, contract.CLI_STORIES_SH):
            raise RunStateError("operator-cli density leaf source is not frozen: "
                                + spec.leaf_id)
        if (spec.leaf_id, spec.site_id) not in zip(ASSERTION_IDS, SITE_IDS):
            raise RunStateError("operator-cli density catalog identity changed: "
                                + spec.leaf_id)
        text = texts[spec.source_name]
        lines = text.splitlines()
        helper_start, helper_end, _body = _function_span(text, spec.helper)
        if not (helper_start <= spec.line_start <= spec.line_end <= helper_end):
            raise RunStateError("operator-cli density anchor escapes its helper: "
                                + spec.leaf_id)
        quote = "\n".join(lines[spec.line_start - 1:spec.line_end])
        if not quote or text.count(quote) != 1:
            raise RunStateError("operator-cli density anchor is not unique on disk: "
                                + spec.leaf_id)
        if spec.comparator not in {"predicate", "equals", "contains",
                                   "contains_any", "exit_zero"}:
            raise RunStateError("operator-cli density comparator is not frozen: "
                                + spec.leaf_id)
        rows.append(Obligation(
            obligation_id=spec.leaf_id, level="DENSITY", method="cli_check",
            line_start=spec.line_start, line_end=spec.line_end, quote=quote,
            parent_obligation=spec.parent_requirement,
            expectation=spec.expectation, operation=spec.site_id,
            field=spec.field, comparator=spec.comparator, expected=spec.expected,
            executor_site_id=spec.site_id, slot=spec.slot, story_label=None,
            source_name=spec.source_name))
    return rows


def obligations() -> tuple[Obligation, ...]:
    """Freeze, validate and return the 28-row source-anchored plan."""
    binding = contract.binding_requirements_face()
    face_sources = dict(binding["sources"])
    frozen_sources = {STORIES_SH: SOURCE_SHA256, **COMPANION_SOURCES}
    if face_sources != frozen_sources:
        raise RunStateError("operator-cli migration contract source digests changed")
    if [row["id"] for row in binding["requirements"]] != list(REQUIREMENTS):
        raise RunStateError("operator-cli parent requirement order changed")
    if [row["command"] for row in binding["requirements"]] != list(COMMANDS):
        raise RunStateError("operator-cli parent command map changed")
    for row in binding["requirements"]:
        if not isinstance(row.get("expectation"), str) or not row["expectation"]:
            raise RunStateError("operator-cli parent expectation face changed")
    paths = _source_paths()
    story_text = _read_source(paths[STORIES_SH], STORIES_SH, SOURCE_SHA256)
    story_lines = story_text.splitlines()
    parents = _build_parents(story_text, binding, story_lines)
    leaves = _build_leaves()
    if tuple(row.obligation_id for row in leaves) != ASSERTION_IDS:
        raise RunStateError("operator-cli density leaf order changed")
    if tuple(row.executor_site_id for row in leaves) != SITE_IDS:
        raise RunStateError("operator-cli density site order changed")
    rows = tuple(parents) + tuple(leaves)
    if (len(parents) != PARENT_OBLIGATION_COUNT or len(leaves) != DENSITY_OBLIGATION_COUNT
            or len(rows) != PARSED_OBLIGATION_COUNT):
        raise RunStateError("operator-cli density layer count changed")
    if len({row.obligation_id for row in rows}) != PARSED_OBLIGATION_COUNT:
        raise RunStateError("operator-cli density obligation IDs are not unique")
    charged = {leaf.parent_obligation for leaf in leaves}
    marked_parents = {parent.obligation_id for parent in parents}
    if charged - marked_parents or set(REQUIREMENTS) != marked_parents:
        raise RunStateError("operator-cli parent envelope changed")
    if {parent for parent in marked_parents if parent not in charged} != set(
            ZERO_ATOM_PARENTS):
        raise RunStateError("operator-cli zero-atom parent disclosure changed")
    # Live dual-face cross-checks: the frozen adapter methods and the frozen
    # once-only CLI operations must equal the committed executor faces.
    from ..operator_cli_runner import (OPERATOR_CLI_CLI_OPERATIONS,
                                        OPERATOR_CLI_EFFECT)
    from ..remote_operator_cli_adapter import (OPERATOR_CLI_ADAPTER_METHODS,
                                               OPERATOR_CLI_CASE_ID)
    if tuple(OPERATOR_CLI_ADAPTER_METHODS) != ADAPTER_METHODS:
        raise RunStateError("operator-cli adapter method face changed")
    if OPERATOR_CLI_CASE_ID != DUAL_CASE_ID:
        raise RunStateError("operator-cli dual case id face changed")
    if tuple(OPERATOR_CLI_CLI_OPERATIONS) != CLI_OPERATIONS:
        raise RunStateError("operator-cli frozen operation face changed")
    if not OPERATOR_CLI_EFFECT:
        raise RunStateError("operator-cli resource disposition face changed")
    return rows


def density_plan() -> dict[str, Any]:
    rows = obligations()
    parents = [row for row in rows if row.level == "PARENT"]
    leaves = [row for row in rows if row.level == "DENSITY"]
    per_parent = {requirement: 0 for requirement in REQUIREMENTS}
    for leaf in leaves:
        per_parent[leaf.parent_obligation] += 1
    if per_parent != EXPECTED_LEAF_COUNTS:
        raise RunStateError("operator-cli per-parent atom counts changed")
    return {
        "schema_version": contract.SCHEMA_VERSION,
        "contract": DENSITY_CONTRACT,
        "parent": contract.PARENT_STORY,
        "family": FAMILY,
        "executor_row_index": EXECUTOR_ROW_INDEX,
        "coverage_row_no": COVERAGE_ROW_NO,
        "source_file": SOURCE_FILE,
        "source_sha256": SOURCE_SHA256,
        "source_files": {name: contract.SOURCE_DISPLAY[name] for name in
                         (STORIES_SH, contract.CLI_SH, contract.CLI_STORIES_SH,
                          contract.GROUP_SH, contract.COMMON_SH)},
        "story_line_span": list(STORY_LINE_SPAN),
        "story_call_span": list(STORY_CALL_SPAN),
        "companion_sources": dict(COMPANION_SOURCES),
        "migration_contract": MIGRATION_CONTRACT,
        "contract_revision": CONTRACT_REVISION,
        "parsed_obligation_count": PARSED_OBLIGATION_COUNT,
        "parent_obligation_count": PARENT_OBLIGATION_COUNT,
        "density_obligation_count": DENSITY_OBLIGATION_COUNT,
        "residual_count_gap": RESIDUAL_COUNT_GAP,
        "delta": DENSITY_OBLIGATION_COUNT,
        "projection_rule": (
            "13 frozen OPERATOR-CLI.* contract parents quote their ordered "
            "stories.sh helper call lines (1786-1798) plus 15 verbatim shell "
            "assertion spans rebuilt from the frozen cli.sh/cli-stories.sh "
            "bytes in story order (ordered identity OPCLI catalog 15/15 with "
            "the site envelope OPERATOR-CLI.*); the exit-zero fail guards, "
            "TESTS_* bookkeeping, ensure_cli_token skip guards, the visibility "
            "original-value capture guard, the upstream register prerequisite "
            "legs and the chat prerequisite friendship admissions stay "
            "parent-charged, and OPERATOR-CLI.create-group is the single "
            "disclosed zero-atom parent because its group.sh helper surface "
            "lives outside the stories.sh/cli.sh/cli-stories.sh upstream "
            "parse face"),
        "obligations": [row.as_dict() for row in rows],
        "parent_ids": [row.obligation_id for row in parents],
        "leaf_ids": [row.obligation_id for row in leaves],
        "per_parent_atom_counts": dict(per_parent),
        "parsed_unlocated_count": 0,
        "parsed_unlocated": [],
        "not_charged_source_probes": [NOT_CHARGED_PROBES],
        "zero_atom_parents": dict(ZERO_ATOM_PARENTS),
        "assertion_ids": list(ASSERTION_IDS),
        "site_ids": list(SITE_IDS),
        "helper_file_map": dict(HELPERS_FILE_MAP),
        "parent_call_lines": list(PARENT_CALL_LINES),
        "executor_catalog": {
            "source": ("bcs_e2e.local_operator_cli_contract.REQUIREMENTS plus "
                       "the frozen operator_cli_density span catalog"),
            "parent_count": PARENT_OBLIGATION_COUNT,
            "atom_count": DENSITY_OBLIGATION_COUNT,
            "ordered_identity": True,
            "case_id": CASE_ID,
            "suite": SUITE,
            "local_status": LOCAL_STATUS,
            "local_executor": LOCAL_EXECUTOR,
            "dual_command": DUAL_COMMAND,
            "dual_case_id": DUAL_CASE_ID,
            "dual_suite": DUAL_SUITE,
            "profile_contract": PROFILE_CONTRACT,
            "report_contract": REPORT_CONTRACT,
            "adapter_methods": list(ADAPTER_METHODS),
            "cli_operations": list(CLI_OPERATIONS),
            "cli_operations_count": len(CLI_OPERATIONS),
            "source_case_id": DUAL_CASE_ID,
            "source_suite": DUAL_SUITE},
        "upstream_issue": contract.UPSTREAM_ISSUE,
        "harness": {
            "driver": "apps/bcs/scripts/e2e-test/e2e.sh",
            "story_call_surface": (
                "apps/bcs/scripts/e2e-test/stories.sh story function body"),
            "cli_helper_surface": (
                "apps/bcs/scripts/e2e-test/cli.sh E2E_TESTS_CLI (11 entries; "
                "the group-create leg lives in group.sh and the direct-onboard "
                "journey helper lives in cli-stories.sh)"),
            "counters": ["TESTS_PASSED", "TESTS_FAILED", "TESTS_TOTAL"],
            "shell_assertion_helpers": ["_cli_contains", "assert_json_eq",
                                        "require_status", "assert_not_empty"]},
        "story_arguments": {"required": [], "optional": [],
                            "basis": "the shell story function takes no "
                                     "arguments; role UUIDs arrive through the "
                                     "trusted-team fixture environment"},
        "story_marker": {"present": False, "keys": [], "patterns": [],
                         "marker": None,
                         "basis": "shell harness stories carry no executor "
                                  "marker print; TESTS_* counter bookkeeping "
                                  "stays parent-charged"},
        "mock_child": {"present": False,
                       "basis": "the story drives the owned local BCS stack "
                                "and the reviewed bcs-cli binary directly; no "
                                "separate mock child process exists"},
        "named_preprod_gaps": list(PREPROD_GAPS),
        "source_read_only": True,
        "remote_validation": "NOT_RUN",
        "grants_release_credit": False,
        "credential_references": [HUMAN_COOKIE_ENV, DRIVER_TOKEN_ENV],
        "upstream_issue_disclosed": True,
    }
