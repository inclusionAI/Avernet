"""Governance gate: ``docs/arch/waivers.md`` is a real, time-bounded register.

``ci.enforce.md`` §H requires that a waiver be owned, dated, name the exact
rule, document a removal plan, and be linked from its PR — and that "expired
waivers trigger review failure or warning escalation". §L schedules "add waiver
process" in Phase 2 and "automated expiry reminders for waivers" in Phase 3;
this file is the smallest Phase 3 slice that keeps the Phase 2 register honest.
Without it a ``Review by`` date is decorative: nothing reads the file, so a
lapsed exception stays permanent and silent.

Three checks:

1. **Shape** — every waiver carries all required fields and prose sections.
   A waiver missing its owner or removal plan is not a waiver.
2. **Expiry** — no ``Active`` waiver is past its ``Review by`` date. This test
   is designed to start failing on that date; that failure *is* the review
   trigger, and the fix is to remove the exception or re-date the waiver with a
   fresh decision, never to bump the date to silence CI.
3. **Coupling** — each Active waiver's declared exception actually appears in
   the gate it claims to waive, so the register cannot drift from the
   allowlist it governs.

Note the coupling is deliberately one-directional (waiver → gate, not
gate → waiver). The ``core → api`` allowlist carries six entries that predate
this register and hold no waiver; requiring the reverse would fail them all,
which is a governance decision for their owners rather than something this
gate should force.
"""

from __future__ import annotations

import datetime
import pathlib
import re

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]          # .../src/backend
_REPO_ROOT = _BACKEND_ROOT.parents[1]          # repo root
_REGISTER = _REPO_ROOT / "docs" / "arch" / "waivers.md"
_COMPLIANCE_GATE = _THIS_FILE.parent / "test_architecture_compliance.py"

_REQUIRED_FIELDS = (
    "Status",
    "Rule violated",
    "Gate",
    "Exception",
    "Owner",
    "Granted",
    "Review by",
)
_REQUIRED_SECTIONS = (
    "Reason",
    "Risk introduced",
    "Compensating controls",
    "Removal plan",
    "Linked PR",
)
_VALID_STATUSES = frozenset({"Active", "Retired"})

_WAIVER_HEADING_RE = re.compile(r"^## (W-\d{3})\b.*$", flags=re.MULTILINE)
_FIELD_ROW_RE = re.compile(r"^\|\s*\*\*(?P<key>[^*]+)\*\*\s*\|\s*(?P<value>.+?)\s*\|\s*$", flags=re.MULTILINE)
_SECTION_RE = re.compile(r"^### (?P<title>.+?)\s*$", flags=re.MULTILINE)


def _register_text() -> str:
    return _REGISTER.read_text(encoding="utf-8")


def _waiver_blocks() -> dict[str, str]:
    """Split the register into ``{waiver_id: block_text}``."""
    text = _register_text()
    matches = list(_WAIVER_HEADING_RE.finditer(text))
    blocks: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks[m.group(1)] = text[m.start():end]
    return blocks


def _fields(block: str) -> dict[str, str]:
    return {m.group("key").strip(): m.group("value").strip() for m in _FIELD_ROW_RE.finditer(block)}


def _sections(block: str) -> dict[str, str]:
    """Return ``{section_title: body}`` for the ``###`` sections in one block."""
    matches = list(_SECTION_RE.finditer(block))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block)
        out[m.group("title").strip()] = block[m.end():end].strip()
    return out


def _strip_code_ticks(value: str) -> str:
    return value.replace("`", "").strip()


@pytest.mark.unit
def test_register_exists_and_is_not_empty() -> None:
    """Guard the guard — a moved or emptied register would pass everything."""
    assert _REGISTER.is_file(), f"waiver register not found at {_REGISTER}"
    assert _waiver_blocks(), (
        f"{_REGISTER} declares no W-### waivers. If every waiver has been "
        "retired, delete the register rather than leaving it empty."
    )


@pytest.mark.unit
def test_every_waiver_declares_all_required_fields() -> None:
    """A waiver missing its owner, date or removal plan is not a waiver."""
    failures: list[str] = []
    for waiver_id, block in sorted(_waiver_blocks().items()):
        fields = _fields(block)
        sections = _sections(block)
        for key in _REQUIRED_FIELDS:
            if not fields.get(key):
                failures.append(f"{waiver_id}: missing required field '{key}'")
        for title in _REQUIRED_SECTIONS:
            if not sections.get(title):
                failures.append(f"{waiver_id}: missing or empty '### {title}' section")
        status = fields.get("Status", "")
        if status and status not in _VALID_STATUSES:
            failures.append(
                f"{waiver_id}: Status '{status}' not one of {sorted(_VALID_STATUSES)}"
            )
    if failures:
        pytest.fail("\n".join(failures))


@pytest.mark.unit
def test_no_active_waiver_is_past_its_review_date() -> None:
    """``ci.enforce.md`` §H: expired waivers must fail, not linger.

    When this fails the exception has outlived its review date. Remove the
    exception, or re-date the waiver as a fresh, owned decision — do not bump
    the date purely to turn CI green.
    """
    today = datetime.date.today()
    failures: list[str] = []
    for waiver_id, block in sorted(_waiver_blocks().items()):
        fields = _fields(block)
        if fields.get("Status") != "Active":
            continue
        raw = _strip_code_ticks(fields.get("Review by", ""))
        try:
            review_by = datetime.date.fromisoformat(raw)
        except ValueError:
            failures.append(
                f"{waiver_id}: 'Review by' value {raw!r} is not an ISO date (YYYY-MM-DD)"
            )
            continue
        if review_by < today:
            failures.append(
                f"{waiver_id}: review date {review_by.isoformat()} has passed "
                f"(today {today.isoformat()}) — re-decide or remove the exception"
            )
    if failures:
        pytest.fail("\n".join(failures))


@pytest.mark.unit
def test_active_waivers_match_a_real_allowlist_entry() -> None:
    """The exception a waiver names must exist in the gate it claims to waive.

    Catches a waiver that outlives the allowlist entry it governs, or one that
    describes an exception nobody ever added.
    """
    gate_source = _COMPLIANCE_GATE.read_text(encoding="utf-8")
    failures: list[str] = []
    for waiver_id, block in sorted(_waiver_blocks().items()):
        fields = _fields(block)
        if fields.get("Status") != "Active":
            continue
        exception = fields.get("Exception", "")
        # "`<rel path>` → `<imported module>`"
        parts = [_strip_code_ticks(p) for p in re.split(r"[→>]", exception) if p.strip()]
        if len(parts) != 2:
            failures.append(
                f"{waiver_id}: 'Exception' must read '`<file>` → `<module>`', got {exception!r}"
            )
            continue
        rel_path, module = parts
        for needle, label in ((rel_path, "file"), (module, "module")):
            if f'"{needle}"' not in gate_source:
                failures.append(
                    f"{waiver_id}: {label} {needle!r} is not present in "
                    f"{_COMPLIANCE_GATE.name} — the waiver and the gate disagree"
                )
    if failures:
        pytest.fail("\n".join(failures))
