"""Frozen-anchor catalog for the dynamic fixed-Loop density families (E2a).

Extracted losslessly from contract.py by the E2a density split: every frozen
file table entry, SHA-256 freeze, site/row caliber, symbolic identity,
dual-arm story literal, the Obligation/SiteSpan dataclasses and the
SHA-verified byte loaders / AST span readers live here so the frozen data
plane is separable from the validation and gating plane.  contract.py
re-exports this entire surface unchanged (its own __all__ is byte-identical),
so every existing `from .contract import ...` keeps resolving.  Nothing here
opens a transport, and the SHA-256 freezes anchor the upstream story bytes,
the two migration-contract JSONs, common.sh and the Provider/Judge mock --
never this package's own source bytes.
"""
from __future__ import annotations

import ast
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..local_fixed_loop_contract import ASSERTION_IDS as FIXED_ASSERTION_IDS
from ..local_nested_fixed_loop_contract import ASSERTION_IDS as NESTED_ASSERTION_IDS
from ..run_state import RunStateError

SCHEMA_VERSION = 1
DENSITY_CONTRACT = "fixed-loop-density-v1"

STORY_FIXED = "story_fixed_loop_human_editor_workflow"
STORY_NESTED = "story_nested_fixed_loop_workflows"
FAMILY_FIXED = "fixed_loop"
FAMILY_NESTED = "nested_fixed_loop"

FIXED_SH_FILE = "apps/bcs/scripts/e2e-test/fixed_loop_story.sh"
FIXED_PY_FILE = "apps/bcs/scripts/e2e-test/fixed_loop_story.py"
NESTED_PY_FILE = "apps/bcs/scripts/e2e-test/nested_fixed_loop_story.py"
FIXED_CONTRACT_FILE = "apps/bcs/scripts/e2e-runner/fixed-loop-migration-contract.json"
NESTED_CONTRACT_FILE = "apps/bcs/scripts/e2e-runner/nested-fixed-loop-migration-contract.json"

RUNNER_ROOT = Path(__file__).resolve().parents[2]
E2E_TEST_DIR = RUNNER_ROOT.parent / "e2e-test"
FIXED_SH_PATH = E2E_TEST_DIR / "fixed_loop_story.sh"
FIXED_PY_PATH = E2E_TEST_DIR / "fixed_loop_story.py"
NESTED_PY_PATH = E2E_TEST_DIR / "nested_fixed_loop_story.py"
FIXED_CONTRACT_JSON_PATH = RUNNER_ROOT / "fixed-loop-migration-contract.json"
NESTED_CONTRACT_JSON_PATH = RUNNER_ROOT / "nested-fixed-loop-migration-contract.json"

# Frozen upstream SHA-256 (cross-checked against the committed migration
# contracts; any drift fails closed before the roster is usable).
FIXED_PY_SHA256 = "8fc6daeace717e1e030478c398ed1a770df192c69fcd7622cd34cbdca11d17d5"
NESTED_PY_SHA256 = "f50edb9c4fc32e8ec3f693f433284a9eaea19a795d55bd87932aedba52c90091"
FIXED_SH_SHA256 = "14939d995cd1134b6be626a44f823a53dcf134f5b702187e0d8639fb6b15b92d"
FIXED_CONTRACT_JSON_SHA256 = "54d9ae079ac3587cff43da855b65e2af7e5f934021ba7e7e6b7d88c7ba9ec3ca"
NESTED_CONTRACT_JSON_SHA256 = "faa2d50075559a9206331bb41d21224fccdff853daaaac3381e78a75e58c7fcf"
COMMON_SH_SHA256 = "44f5445031e82cfb06b75fc35721eb4ed4338539f04ec7364b35386a3fe923b0"
PROVIDER_JUDGE_MOCK_SHA256 = "fe56c8c2d79ce141fe1d2784cc21c184c90000bb8e6908c976168f3bdfad70c4"
SOURCE_REVISION = "8559a7a5258a2cb6750befec187e50aa85060cc6"

FIXED_SH_STORY_SPAN = (20, 32)
NESTED_SH_STORY_SPAN = (34, 44)
FIXED_SH_ASSERT_LINE = 31
NESTED_SH_ASSERT_LINE = 43

# Calibers: sites (executor ledger + shell facade), charged rows, leaves.
FIXED_ASSERT_SITE_COUNT = 33
FIXED_TRANSPORT_SITE_COUNT = 33
FIXED_LEDGER_SITE_COUNT = 66
FIXED_SITE_COUNT = FIXED_LEDGER_SITE_COUNT + 1
FIXED_PARENT_COUNT = 8
FIXED_LEAF_COUNT = 34
FIXED_CHARGED_ROW_COUNT = 42
FIXED_AUX_ENVELOPE_COUNT = 1
FIXED_ROSTER_ROW_COUNT = FIXED_CHARGED_ROW_COUNT + FIXED_AUX_ENVELOPE_COUNT
NESTED_ASSERT_SITE_COUNT = 24
NESTED_TRANSPORT_SITE_COUNT = 22
NESTED_LEDGER_SITE_COUNT = 46
NESTED_SITE_COUNT = NESTED_LEDGER_SITE_COUNT + 1
NESTED_PARENT_COUNT = 16
NESTED_LEAF_COUNT = 25
NESTED_CHARGED_ROW_COUNT = 41
NESTED_AUX_ENVELOPE_COUNT = 1
NESTED_ROSTER_ROW_COUNT = NESTED_CHARGED_ROW_COUNT + NESTED_AUX_ENVELOPE_COUNT
FAMILY_LEAF_COUNT = FIXED_LEAF_COUNT + NESTED_LEAF_COUNT
FAMILY_PARENT_COUNT = FIXED_PARENT_COUNT + NESTED_PARENT_COUNT
FAMILY_CHARGED_ROW_COUNT = FIXED_CHARGED_ROW_COUNT + NESTED_CHARGED_ROW_COUNT
FAMILY_ROSTER_ROW_COUNT = FIXED_ROSTER_ROW_COUNT + NESTED_ROSTER_ROW_COUNT
FAMILY_SITE_COUNT = FIXED_SITE_COUNT + NESTED_SITE_COUNT
FAMILY_LEDGER_SITE_COUNT = FIXED_LEDGER_SITE_COUNT + NESTED_LEDGER_SITE_COUNT
RESIDUAL_COUNT_GAP = 0

# Face (executor-migration.json / coverage-map) calibers this wave closes.
FIXED_FACE_PARSED = 1
FIXED_FACE_OBLIGATIONS = 8
FIXED_FACE_GAP = FIXED_FACE_PARSED - FIXED_FACE_OBLIGATIONS
NESTED_FACE_PARSED = 1
NESTED_FACE_OBLIGATIONS = 16
NESTED_FACE_GAP = NESTED_FACE_PARSED - NESTED_FACE_OBLIGATIONS

UPSTREAM_NOT_ROSTERED = "NOT_ROSTERED"
B_UPSTREAM_NOT_ROSTERED = (
    "no per-check poolab roster receipt is retained for the dynamic owned-local "
    "fixed-Loop families; the face coverage-map rows carry only the aggregate "
    "0-green/1-parsed facade verdict, which is itself the disclosed undercount; "
    "colors are never borrowed from envelope-level snapshots (negative-evidence "
    "discipline)")

GATE_ID_FIXED = "FIXED_LOOP.story-gate"
GATE_ID_NESTED = "NESTED-LOOP.story-gate"
FIXED_WRAPPER_LEAF = "FLOOP.F01"
NESTED_WRAPPER_LEAF = "NFLOOP.F01"
FIXED_PREFIX = "FLOOP.F"
NESTED_PREFIX = "NFLOOP.F"

DRIVER_TOKEN_ENV = "BCS_E2E_DRIVER_TOKEN"

FIXED_CONTRACT_ENVELOPE = "fixed_loop_migration_contract"
NESTED_CONTRACT_ENVELOPE = "nested_fixed_loop_migration_contract"
FACE_WRAPPER_GATE = "face_wrapper_gate"

LOCAL_EXECUTION = "owned-local-offline-replay"
PREPROD_EXECUTION = "preprod-not-run"
PREPROD_INJECTED_EXECUTION = "preprod-injected-observation-projection"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_NOT_RUN = "NOT_RUN"
STATUS_BLOCKED_INVENTORIED = "BLOCKED_INVENTORIED"
BLOCKED_AFTER_ASSERTION_FAILURE = "BLOCKED_AFTER_ASSERTION_FAILURE"
OBSERVATION_MISMATCH = "OBSERVATION_MISMATCH"

# Frozen symbolic observation identities (never credential values).
SYMBOL_REF_FIRST = "response_ref:first-pending"
SYMBOL_REF_SECOND = "response-ref:second-pending"
SYMBOL_NODE_SECOND = "node-id:second-pending"
SYMBOL_GROUP_ECHO = "echo:bcn-group-id"
SYMBOL_SESSION_ECHO = "echo:session-id"
SYMBOL_ARTIFACT = "echo:node-artifact-text"
SYMBOL_DELIVERY_EARLY = "delivery-id:inner-approval-early-break"
SYMBOL_CREDENTIAL_DOWNLINK = (
    "binding:provider-downlink-static-bearer:bcs-to-provider-token")
SYMBOL_EXECUTION_NODE_FIRST_INNER = "execution-node-id:first-inner-turn"
SYMBOL_HUMAN_OUTPUT_FIRST_INNER = "human-output:first-inner-turn"

# Dual-arm frozen literals from the stories.
FIXED_JUDGE_OUTCOMES = ("revise", "approved", "revise", "revise")
FIXED_FINAL_OUTPUT = "final-editorial-output"
FIXED_FIRST_EDITOR_OUTPUT = "first-editor-result"
FIXED_SECOND_EDITOR_OUTPUT = "second-editor-result"
FIXED_FIRST_HUMAN_INPUT = "first-human-input"
FIXED_SECOND_HUMAN_INPUT = "second-human-input"
NESTED_BOT_JUDGE_OUTCOMES = (
    "revise", "approved", "repeat", "revise", "revise", "revise", "done")
NESTED_HUMAN_JUDGE_OUTCOMES = ("repeat", "done")
NESTED_BOT_STEPS = 15
NESTED_BOT_SKIPPED_FRAMES = (("outer", 1), ("inner", 3))
SHARED_CONTROL_RESETS = ("/control/reset", "/control/provider/clear")


@dataclass(frozen=True)
class Obligation:
    """One roster row: a parent envelope, an auxiliary gate, or a charged leaf."""

    obligation_id: str
    story: str
    level: str
    charged: bool
    envelope_source: str
    parent_obligation: str
    co_parents: tuple
    legs: tuple
    ledger_site_id: str | None
    method: str
    operation: str
    path: str
    actor: str
    kind: str
    field: str | None
    comparator: str
    expected: Any = None
    line_start: int = 0
    line_end: int = 0
    quote: str = ""
    source_file: str = ""
    expectation: str = ""
    upstream_color: str = UPSTREAM_NOT_ROSTERED
    upstream_color_basis: str = B_UPSTREAM_NOT_ROSTERED
    named_registrations: tuple = ()
    leaf_classification: str = "assertion_atom"
    basis: str = ""
    internal_checkpoints: tuple = ()
    internal_assert_sites: tuple = ()
    story_lines: tuple = ()
    markers: tuple = ()
    located: bool = True

    def as_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "obligation_id": self.obligation_id,
            "story": self.story,
            "level": self.level,
            "charged": self.charged,
            "envelope_source": self.envelope_source,
            "parent_obligation": self.parent_obligation,
            "co_parents": list(self.co_parents),
            "legs": list(self.legs),
            "ledger_site_id": self.ledger_site_id,
            "method": self.method,
            "operation": self.operation,
            "path": self.path,
            "actor": self.actor,
            "kind": self.kind,
            "field": self.field,
            "comparator": self.comparator,
            "expected": (list(self.expected) if isinstance(self.expected, tuple)
                         else self.expected),
            "line_start": self.line_start,
            "line_end": self.line_end,
            "quote": self.quote,
            "source_file": self.source_file,
            "expectation": self.expectation,
            "upstream_color": self.upstream_color,
            "upstream_color_basis": self.upstream_color_basis,
            "named_registrations": list(self.named_registrations),
            "leaf_classification": self.leaf_classification,
            "basis": self.basis,
            "internal_checkpoints": list(self.internal_checkpoints),
            "internal_assert_sites": list(self.internal_assert_sites),
            "story_lines": list(self.story_lines),
            "markers": list(self.markers),
            "located": self.located,
        }
        return value


@dataclass(frozen=True)
class SiteSpan:
    """One executor-ledger site: function, label, kind and source span."""

    site_id: str
    function: str
    label: str
    kind: str
    start: int
    end: int


def _frozen_lines(path: Path, sha256: str, label: str) -> list[str]:
    if path.is_symlink() or not path.is_file():
        raise RunStateError(f"fixed-loop density {label} source is unavailable")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise RunStateError(f"fixed-loop density {label} source SHA-256 drifted")
    return raw.decode("utf-8").splitlines()


def _collect_site_records(text: str, prefix: str) -> list[SiteSpan]:
    """Re-derive the executor ledger sites with exact AST spans.

    Mirrors local_fixed_loop_contract._story_assertion_sites one-to-one (same
    visitor, same cursors, same id grammar, weighted=False) while recording
    each site's span; the caller asserts id parity with the frozen ledger.
    """
    records: list[SiteSpan] = []
    cursors: dict[tuple[str, str, int], int] = {}

    def add(function: str, label: str, line: int, end: int, kind: str) -> None:
        key = (function, label, line)
        start_index = cursors.get(key, 0)
        records.append(SiteSpan(
            site_id=f"{prefix}{function}.{label}-{line:03d}-{start_index + 1:02d}",
            function=function, label=label, kind=kind, start=line, end=end))
        cursors[key] = start_index + 1

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.fn = "module"

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            previous = self.fn
            self.fn = node.name
            self.generic_visit(node)
            self.fn = previous

        def visit_Assert(self, node: ast.Assert) -> None:
            if self.fn not in {"request", "post", "browser", "cli"}:
                add(self.fn, "assert", node.lineno, node.end_lineno, "assert")
            self.generic_visit(node)

        def visit_Call(self, node: ast.Call) -> None:
            name = getattr(node.func, "attr", "")
            if (name in {"request", "post", "browser", "cli", "check"}
                    and self.fn not in {"request", "post", "browser", "cli"}):
                add(self.fn, name, node.lineno, node.end_lineno, "transport")
            self.generic_visit(node)

    Visitor().visit(ast.parse(text, filename="story-source"))
    return records


def _site_records(family: str) -> tuple[SiteSpan, ...]:
    if family == FAMILY_FIXED:
        text = _frozen_lines(FIXED_PY_PATH, FIXED_PY_SHA256, FIXED_PY_FILE)
        records = _collect_site_records("\n".join(text) + "\n", "FIXED_LOOP.")
        if tuple(record.site_id for record in records) != FIXED_ASSERTION_IDS:
            raise RunStateError("fixed-Loop site ledger parity broke")
        return tuple(records)
    text = _frozen_lines(NESTED_PY_PATH, NESTED_PY_SHA256, NESTED_PY_FILE)
    records = _collect_site_records("\n".join(text) + "\n", "NESTED-LOOP.")
    if tuple(record.site_id for record in records) != NESTED_ASSERTION_IDS:
        raise RunStateError("nested fixed-Loop site ledger parity broke")
    return tuple(records)


def _function_spans(text: str) -> dict[str, tuple[int, int]]:
    tree = ast.parse(text, filename="story-source")
    spans: dict[str, tuple[int, int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in spans and spans[node.name] != (node.lineno, node.end_lineno):
                raise RunStateError(f"ambiguous function span: {node.name}")
            spans[node.name] = (node.lineno, node.end_lineno)
    return spans


def _sh_story_function_span(lines: list[str], story_name: str) -> tuple[int, int]:
    start = None
    for index, line in enumerate(lines):
        if line.startswith(story_name + "() {"):
            start = index + 1
            break
    if start is None:
        raise RunStateError(f"shell story function disappeared: {story_name}")
    for index in range(start, len(lines)):
        if lines[index] == "}":
            return (start, index + 1)
    raise RunStateError(f"shell story function never closes: {story_name}")


def _sh_facade_assert_message(lines: list[str], line_no: int) -> str:
    text = lines[line_no - 1]
    if "assert_eq" not in text or '"$result"' not in text or '"0"' not in text:
        raise RunStateError(f"shell facade assert line drifted: {line_no}")
    first = text.find('"')
    if first < 0:
        raise RunStateError("shell facade assert message is not quotable")
    second = text.find('"', first + 1)
    if second <= first:
        raise RunStateError("shell facade assert message is not quotable")
    message = text[first + 1:second]
    if not message:
        raise RunStateError("shell facade assert message is empty")
    return message


def _json_requirement_span(json_text: str, requirement_id: str) -> tuple[int, int]:
    needle = '"id": "' + requirement_id + '"'
    position = json_text.find(needle)
    if position < 0:
        raise RunStateError(f"migration contract requirement disappeared: {requirement_id}")
    start = json_text.rfind("{", 0, position)
    if start < 0:
        raise RunStateError(f"migration contract requirement object opener missing: {requirement_id}")
    depth = 0
    end = None
    for index in range(start, len(json_text)):
        char = json_text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None or end <= position:
        raise RunStateError(f"migration contract requirement object closer missing: {requirement_id}")
    return (start, end)


def _span_lines(text: str, start_offset: int, end_offset: int) -> tuple[int, int]:
    line_start = text.count("\n", 0, start_offset) + 1
    line_end = text.count("\n", 0, end_offset - 1) + 1
    return (line_start, line_end)


def site_records(family: str) -> tuple[SiteSpan, ...]:
    return _site_records(family)


def site_span_index(family: str) -> dict[str, SiteSpan]:
    return {record.site_id: record for record in _site_records(family)}


def story_lines(family: str) -> tuple[str, ...]:
    """Return the frozen python story lines for independent drift rebuilds."""
    if family == FAMILY_FIXED:
        return tuple(_frozen_lines(FIXED_PY_PATH, FIXED_PY_SHA256, FIXED_PY_FILE))
    return tuple(_frozen_lines(NESTED_PY_PATH, NESTED_PY_SHA256, NESTED_PY_FILE))


def wrapper_sh_story_lines() -> tuple[str, ...]:
    """Return the frozen shell wrapper lines carrying both story facades."""
    return tuple(_frozen_lines(FIXED_SH_PATH, FIXED_SH_SHA256, FIXED_SH_FILE))


__all__ = [
"BLOCKED_AFTER_ASSERTION_FAILURE",
    "B_UPSTREAM_NOT_ROSTERED",
    "COMMON_SH_SHA256",
    "DENSITY_CONTRACT",
    "DRIVER_TOKEN_ENV",
    "E2E_TEST_DIR",
    "FACE_WRAPPER_GATE",
    "FAMILY_CHARGED_ROW_COUNT",
    "FAMILY_FIXED",
    "FAMILY_LEAF_COUNT",
    "FAMILY_LEDGER_SITE_COUNT",
    "FAMILY_NESTED",
    "FAMILY_PARENT_COUNT",
    "FAMILY_ROSTER_ROW_COUNT",
    "FAMILY_SITE_COUNT",
    "FIXED_ASSERT_SITE_COUNT",
    "FIXED_AUX_ENVELOPE_COUNT",
    "FIXED_CHARGED_ROW_COUNT",
    "FIXED_CONTRACT_ENVELOPE",
    "FIXED_CONTRACT_FILE",
    "FIXED_CONTRACT_JSON_PATH",
    "FIXED_CONTRACT_JSON_SHA256",
    "FIXED_FACE_GAP",
    "FIXED_FACE_OBLIGATIONS",
    "FIXED_FACE_PARSED",
    "FIXED_FINAL_OUTPUT",
    "FIXED_FIRST_EDITOR_OUTPUT",
    "FIXED_FIRST_HUMAN_INPUT",
    "FIXED_JUDGE_OUTCOMES",
    "FIXED_LEAF_COUNT",
    "FIXED_LEDGER_SITE_COUNT",
    "FIXED_PARENT_COUNT",
    "FIXED_PREFIX",
    "FIXED_PY_FILE",
    "FIXED_PY_PATH",
    "FIXED_PY_SHA256",
    "FIXED_ROSTER_ROW_COUNT",
    "FIXED_SECOND_EDITOR_OUTPUT",
    "FIXED_SECOND_HUMAN_INPUT",
    "FIXED_SH_ASSERT_LINE",
    "FIXED_SH_FILE",
    "FIXED_SH_PATH",
    "FIXED_SH_SHA256",
    "FIXED_SH_STORY_SPAN",
    "FIXED_SITE_COUNT",
    "FIXED_TRANSPORT_SITE_COUNT",
    "FIXED_WRAPPER_LEAF",
    "GATE_ID_FIXED",
    "GATE_ID_NESTED",
    "LOCAL_EXECUTION",
    "NESTED_ASSERT_SITE_COUNT",
    "NESTED_AUX_ENVELOPE_COUNT",
    "NESTED_BOT_JUDGE_OUTCOMES",
    "NESTED_BOT_SKIPPED_FRAMES",
    "NESTED_BOT_STEPS",
    "NESTED_CHARGED_ROW_COUNT",
    "NESTED_CONTRACT_ENVELOPE",
    "NESTED_CONTRACT_FILE",
    "NESTED_CONTRACT_JSON_PATH",
    "NESTED_CONTRACT_JSON_SHA256",
    "NESTED_FACE_GAP",
    "NESTED_FACE_OBLIGATIONS",
    "NESTED_FACE_PARSED",
    "NESTED_HUMAN_JUDGE_OUTCOMES",
    "NESTED_LEAF_COUNT",
    "NESTED_LEDGER_SITE_COUNT",
    "NESTED_PARENT_COUNT",
    "NESTED_PREFIX",
    "NESTED_PY_FILE",
    "NESTED_PY_PATH",
    "NESTED_PY_SHA256",
    "NESTED_ROSTER_ROW_COUNT",
    "NESTED_SH_ASSERT_LINE",
    "NESTED_SH_STORY_SPAN",
    "NESTED_SITE_COUNT",
    "NESTED_TRANSPORT_SITE_COUNT",
    "NESTED_WRAPPER_LEAF",
    "OBSERVATION_MISMATCH",
    "Obligation",
    "PREPROD_EXECUTION",
    "PREPROD_INJECTED_EXECUTION",
    "PROVIDER_JUDGE_MOCK_SHA256",
    "RESIDUAL_COUNT_GAP",
    "RUNNER_ROOT",
    "SCHEMA_VERSION",
    "SHARED_CONTROL_RESETS",
    "SOURCE_REVISION",
    "STATUS_BLOCKED_INVENTORIED",
    "STATUS_FAIL",
    "STATUS_NOT_RUN",
    "STATUS_PASS",
    "STORY_FIXED",
    "STORY_NESTED",
    "SYMBOL_ARTIFACT",
    "SYMBOL_CREDENTIAL_DOWNLINK",
    "SYMBOL_DELIVERY_EARLY",
    "SYMBOL_EXECUTION_NODE_FIRST_INNER",
    "SYMBOL_GROUP_ECHO",
    "SYMBOL_HUMAN_OUTPUT_FIRST_INNER",
    "SYMBOL_NODE_SECOND",
    "SYMBOL_REF_FIRST",
    "SYMBOL_REF_SECOND",
    "SYMBOL_SESSION_ECHO",
    "SiteSpan",
    "UPSTREAM_NOT_ROSTERED",
    "site_records",
    "site_span_index",
    "story_lines",
    "wrapper_sh_story_lines",
]
