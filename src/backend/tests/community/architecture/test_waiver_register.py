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


def _exception_pair(block: str) -> tuple[str, str] | None:
    """Parse an ``Exception`` row of the form "`<rel path>` → `<module>`"."""
    exception = _fields(block).get("Exception", "")
    parts = [_strip_code_ticks(p) for p in re.split(r"[→>]", exception) if p.strip()]
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _gate_source() -> str:
    return _COMPLIANCE_GATE.read_text(encoding="utf-8")


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
    gate_source = _gate_source()
    failures: list[str] = []
    for waiver_id, block in sorted(_waiver_blocks().items()):
        if _fields(block).get("Status") != "Active":
            continue
        pair = _exception_pair(block)
        if pair is None:
            failures.append(
                f"{waiver_id}: 'Exception' must read '`<file>` → `<module>`', "
                f"got {_fields(block).get('Exception', '')!r}"
            )
            continue
        rel_path, module = pair
        for needle, label in ((rel_path, "file"), (module, "module")):
            if f'"{needle}"' not in gate_source:
                failures.append(
                    f"{waiver_id}: {label} {needle!r} is not present in "
                    f"{_COMPLIANCE_GATE.name} — the waiver and the gate disagree"
                )
    if failures:
        pytest.fail("\n".join(failures))


@pytest.mark.unit
def test_retired_waivers_have_released_their_exceptions() -> None:
    """Retiring a waiver must remove the exception, not just relabel the row.

    Without this, ``Status: Retired`` is an escape hatch: the expiry and
    active-coupling checks both skip non-Active waivers, so flipping the field
    would leave the allowlist entry in place, permanently and ungoverned —
    the same move as bumping a review date to silence CI, by another field.
    """
    gate_source = _gate_source()
    failures: list[str] = []
    for waiver_id, block in sorted(_waiver_blocks().items()):
        if _fields(block).get("Status") != "Retired":
            continue
        pair = _exception_pair(block)
        if pair is None:
            continue  # shape is reported by the field test
        rel_path, module = pair
        still_present = [n for n in (rel_path, module) if f'"{n}"' in gate_source]
        if still_present:
            failures.append(
                f"{waiver_id} is Retired but its exception is still allowlisted in "
                f"{_COMPLIANCE_GATE.name}: {still_present}. Remove the allowlist "
                "entry (and the import it permits), or set the waiver back to "
                "Active with a fresh review date."
            )
    if failures:
        pytest.fail("\n".join(failures))


@pytest.mark.unit
def test_waivers_the_gate_cites_exist_and_are_active() -> None:
    """Every ``W-###`` the gate names must resolve to an Active waiver.

    Closes the deletion path: without this, removing W-001's block from the
    register while leaving its allowlist entry — which advertises itself as
    "Governed by waiver W-001" — would silently pass every other check.

    This reverse-maps only exceptions that *claim* governance, so the six
    pre-existing ``core → api`` entries that cite no waiver are untouched.
    Requiring a waiver for those is a governance decision for their owners.
    """
    cited = set(re.findall(r"W-\d{3}", _gate_source()))
    blocks = _waiver_blocks()
    failures: list[str] = []
    for waiver_id in sorted(cited):
        block = blocks.get(waiver_id)
        if block is None:
            failures.append(
                f"{_COMPLIANCE_GATE.name} cites {waiver_id}, but no such waiver "
                f"exists in {_REGISTER.name} — the exception is ungoverned"
            )
            continue
        status = _fields(block).get("Status")
        if status != "Active":
            failures.append(
                f"{_COMPLIANCE_GATE.name} cites {waiver_id}, but its Status is "
                f"{status!r}, not 'Active' — an allowlist entry may not cite a "
                "non-active waiver"
            )
    if failures:
        pytest.fail("\n".join(failures))
