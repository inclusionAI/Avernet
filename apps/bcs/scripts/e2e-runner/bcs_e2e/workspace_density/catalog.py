"""Frozen-anchor catalog for the group workspace density roster.

Extracted losslessly from contract.py by the workspace-density plane split
(the fixed_loop_density E2a split pattern, completing the owning wave's
frozen.py/plumbing.py extraction).  The Obligation dataclass, the SHA-256
gated loaders for the five frozen inputs (group.sh, stories.sh, the 63-atom
density oracle, the 43-obligation workspace-field ledger and
migration-contracts.json S07), the shell function-span reader, the fold and
quote verifier and the canonical atom address key live here, so the frozen
data plane is separable from classification, gating and assembly.  This
module opens no transport; the SHA-256 freezes anchor the upstream shells
and the three committed JSON inputs, never this package's own source bytes.
contract.py re-exports the entire historical surface unchanged, so every
pre-split ``from ...workspace_density.contract import ...`` name keeps
resolving.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..run_state import RunStateError
from .frozen import (
    ATOM_COUNT, COMMITTED_FIELD_BASELINE, COMMITTED_FIELD_ROWS,
    COMMITTED_FIELD_TOTAL, FIELD_ORACLE_CONTRACT, FIELD_ORACLE_PATH,
    FIELD_ORACLE_SHA256, FUNCTION_OPEN, GROUP_SH_PATH, GROUP_SH_SHA256,
    GROUP_SOURCE_FILE, MIGRATION_PATH, MIGRATION_SHA256, ORACLE_CONTRACT,
    ORACLE_PATH, ORACLE_SHA256, PARENT, PARENT_COUNT, STORIES_SH_PATH,
    STORIES_SH_SHA256, STORIES_SOURCE_FILE,
)
@dataclass(frozen=True)
class Obligation:
    """One parent or field-level obligation with an exact source anchor."""

    obligation_id: str
    parent_obligation: str
    level: str
    family: str
    method: str
    line_start: int
    line_end: int
    quote: str
    expectation: str
    operation: str
    field_subject: str
    basis: str
    expected: Any
    density: bool
    source_file: str
    source_function: str | None
    address: dict[str, Any] | None
    atom_id: str | None
    story_calls: tuple[int, ...]
    additional_story_calls: tuple[dict[str, Any], ...]
    committed_field_obligation: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "obligation_id": self.obligation_id,
            "parent_obligation": self.parent_obligation,
            "level": self.level,
            "family": self.family,
            "method": self.method,
            "source": {
                "file": self.source_file,
                "line_start": self.line_start,
                "line_end": self.line_end,
                "quote": self.quote,
                "source_function": self.source_function,
                "located": True,
            },
            "story_calls": list(self.story_calls),
            "additional_story_calls": [dict(item) for item in self.additional_story_calls],
            "operation": self.operation,
            "field_subject": self.field_subject,
            "basis": self.basis,
            "expected": self.expected,
            "expectation": self.expectation,
            "density": self.density,
            "atom_id": self.atom_id,
            "committed_field_obligation": self.committed_field_obligation,
            "address": dict(self.address) if self.address else None,
        }


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_gated(path: Path, expected_sha: str, label: str) -> tuple[bytes, list[str]]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise RunStateError(f"workspace density {label} source unavailable: {error}") from None
    if _sha256(raw) != expected_sha:
        raise RunStateError(f"workspace density {label} sha256 drifted")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise RunStateError(f"workspace density {label} is not UTF-8: {error}") from None
    return raw, lines


def _load_json_gated(path: Path, expected_sha: str, label: str) -> dict[str, Any]:
    raw, _ = _read_gated(path, expected_sha, label)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError) as error:
        raise RunStateError(f"workspace density {label} is not valid JSON: {error}") from None
    return value


def group_sh_lines() -> list[str]:
    return _read_gated(GROUP_SH_PATH, GROUP_SH_SHA256, "group.sh")[1]


def stories_sh_lines() -> list[str]:
    return _read_gated(STORIES_SH_PATH, STORIES_SH_SHA256, "stories.sh")[1]


def load_oracle() -> dict[str, Any]:
    oracle = _load_json_gated(ORACLE_PATH, ORACLE_SHA256, "density oracle")
    if oracle.get("schema_version") != 1 or oracle.get("contract") != ORACLE_CONTRACT:
        raise RunStateError("workspace density oracle identity changed")
    if oracle.get("parent_story") != PARENT:
        raise RunStateError("workspace density oracle parent story changed")
    if oracle.get("atom_count") != ATOM_COUNT:
        raise RunStateError("workspace density oracle atom count changed")
    rows = oracle.get("density_requirements")
    if not isinstance(rows, list) or len(rows) != ATOM_COUNT:
        raise RunStateError("workspace density oracle requirement table changed")
    ids = [row.get("id") for row in rows]
    if len(set(ids)) != ATOM_COUNT or not all(isinstance(item, str) for item in ids):
        raise RunStateError("workspace density oracle atom ids changed")
    families = [row.get("family") for row in rows]
    if sorted(families) != sorted(
            ["api"] * 26 + ["cli"] * 11 + ["tail"] * 26):
        raise RunStateError("workspace density oracle family census changed")
    for row in rows:
        for key in ("expectation", "source_file", "source_function", "source_quote",
                    "address", "checks", "line_start", "line_end"):
            if key not in row:
                raise RunStateError("workspace density oracle row lost its " + key)
        if row["source_file"] not in (GROUP_SOURCE_FILE, STORIES_SOURCE_FILE):
            raise RunStateError("workspace density oracle source file drifted")
    embedded = oracle.get("sources") or {}
    if embedded.get(GROUP_SOURCE_FILE) != GROUP_SH_SHA256 or embedded.get(STORIES_SOURCE_FILE) != STORIES_SH_SHA256:
        raise RunStateError("workspace density oracle embedded source sha256 cross-freeze mismatch")
    if embedded.get("source_root") != "e2e-test":
        raise RunStateError("workspace density oracle source root drifted")
    return oracle


def load_field_ledger() -> dict[str, Any]:
    ledger = _load_json_gated(FIELD_ORACLE_PATH, FIELD_ORACLE_SHA256, "field ledger")
    if ledger.get("schema_version") != 1 or ledger.get("contract") != FIELD_ORACLE_CONTRACT:
        raise RunStateError("workspace field ledger identity changed")
    if ledger.get("parent_story") != PARENT:
        raise RunStateError("workspace field ledger parent story changed")
    if ledger.get("atom_count") != ATOM_COUNT:
        raise RunStateError("workspace field ledger atom count changed")
    rows = ledger.get("obligations")
    if not isinstance(rows, list) or len(rows) != COMMITTED_FIELD_TOTAL:
        raise RunStateError("workspace field ledger obligation table changed")
    rollup = ledger.get("baseline_atom_rollup") or {}
    baseline = [row for row in rows if row.get("kind") == "COARSE"]
    fields = [row for row in rows if row.get("kind") == "FIELD"]
    if len(baseline) != COMMITTED_FIELD_BASELINE or len(fields) != COMMITTED_FIELD_ROWS:
        raise RunStateError("workspace field ledger coarse/field decomposition changed")
    return ledger


def load_migration_story() -> dict[str, Any]:
    contracts = _load_json_gated(MIGRATION_PATH, MIGRATION_SHA256, "migration contracts")
    if contracts.get("schema_version") != 1:
        raise RunStateError("migration contracts schema changed")
    stories = contracts.get("stories")
    if not isinstance(stories, list):
        raise RunStateError("migration contracts story table changed")
    story = next((item for item in stories
                  if isinstance(item, dict) and item.get("legacy_id") == PARENT), None)
    if story is None:
        raise RunStateError("migration contracts lost the group workspace story")
    if len(story.get("requirements") or []) != PARENT_COUNT:
        raise RunStateError("migration contracts workspace requirement count changed")
    if len(story.get("steps") or []) != 16:
        raise RunStateError("migration contracts workspace step count changed")
    if len(story.get("corrections") or []) != 3:
        raise RunStateError("migration contracts workspace corrections changed")
    return story


def _function_spans(lines: list[str]) -> dict[str, tuple[int, int]]:
    spans: dict[str, tuple[int, int]] = {}
    name: str | None = None
    start = 0
    for number, line in enumerate(lines, 1):
        if name is None:
            match = FUNCTION_OPEN.match(line)
            if match:
                name = match.group(1)
                start = number
        elif line == "}":
            spans[name] = (start, number)
            name = None
    if name is not None:
        raise RunStateError("workspace density source function never closes")
    return spans


def _span_contains(span: tuple[int, int], row: dict[str, Any]) -> bool:
    return span[0] <= row["line_start"] <= row["line_end"] <= span[1]


def _fold(raw_lines: list[str]) -> str:
    folded: list[str] = []
    for line in raw_lines:
        stripped = line.strip()
        if stripped.endswith("\\"):
            stripped = stripped[:-1].strip()
        folded.append(stripped)
    return " ".join(part for part in folded if part)


def _verify_atom_quotes(rows: list[dict[str, Any]], sources: dict[str, list[str]],
                        spans: dict[str, dict[str, tuple[int, int]]]) -> None:
    for row in rows:
        lines = sources[row["source_file"]]
        span = spans[row["source_file"]].get(row["source_function"])
        if span is None or not _span_contains(span, row):
            raise RunStateError(f"workspace density atom anchor left its function: {row['id']}")
        folded = _fold(lines[row["line_start"] - 1:row["line_end"]])
        quote = row["source_quote"]
        if not quote:
            raise RunStateError(f"workspace density atom quote is empty: {row['id']}")
        if row["line_start"] == row["line_end"]:
            if folded != quote:
                raise RunStateError(f"workspace density atom quote drifted: {row['id']}")
        elif quote not in folded:
            raise RunStateError(f"workspace density atom quote drifted: {row['id']}")


def _operation_display(address: dict[str, Any]) -> str:
    kind = address.get("kind")
    if kind == "operation":
        return str(address.get("operation"))
    if kind == "read":
        template = str(address.get("path_template"))
        after = address.get("after_operation")
        return f"read {template}@{after}" if after else f"read {template}"
    if kind == "state":
        return f"state:{address.get('state_key')}"
    raise RunStateError(f"workspace density atom address kind unknown: {kind}")


def _field_subjects(checks: list[dict[str, Any]]) -> str:
    subjects: list[str] = []
    for check in checks:
        kind = check.get("type")
        if kind in ("status_eq", "not_found_state", "settings_retired_form", "settings_policy_form"):
            subject = "http_status"
        elif kind == "exit_zero":
            subject = "cli_exit_code"
        elif kind == "read_present":
            subject = "read"
        elif kind in ("stdout_contains", "stdout_contains_any", "stdout_regex"):
            subject = "stdout"
        elif kind == "any_of":
            subject = "any_of"
        else:
            subject = str(check.get("path") or kind)
        if subject not in subjects:
            subjects.append(subject)
    return ";".join(subjects)


def address_key(address: dict[str, Any]) -> str:
    """Canonical replay key for one frozen atom address."""
    kind = address.get("kind")
    journal = str(address.get("journal"))
    if kind == "operation":
        return f"op:{journal}:{address.get('operation')}"
    if kind == "read":
        after = address.get("after_operation")
        suffix = f"@{after}" if after else "#unsequenced"
        return f"read:{journal}:{address.get('path_template')}{suffix}"
    if kind == "state":
        return f"state:{journal}:{address.get('state_key')}"
    raise RunStateError(f"workspace density atom address kind unknown: {kind}")
