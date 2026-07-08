"""Rule 9 — Single Responsibility — file-size guard.

Files exceeding ~1000 lines almost always violate single
responsibility: at that size a service / router / repository is
juggling multiple cohesive concerns that belong in their own modules.
Splitting them is multi-day refactoring work — this test does not
force the split, it just **prevents new debt** from accumulating.

Contract enforced by this test:

1. **No new oversized file.** Any ``.py`` under ``src/agentclaw/``
   that crosses the threshold without being on the allowlist fails.

2. **Shrinking allowlist.** Each allowlist entry must still exist on
   disk AND still be over the threshold. If a file drops below the
   threshold (good — someone split it), its allowlist entry becomes
   stale and must be deleted in the same PR. This forces the debt
   counter to monotonically shrink.

Threshold: 1000 lines (matches the May-26 backend-arch diagnosis
that cataloged the original ``R9 ⚠️`` set). Physical line count
(``len(text.splitlines())``) — same metric ``wc -l`` reports, so
the numbers in the allowlist are reproducible at the command line:

    find src/agentclaw -name '*.py' -not -path '*/__pycache__/*' \\
        -exec wc -l {} \\; | awk '$1 >= 1000'

The allowlist tracks the 20 files that were over 1000 lines on the
day this guard was introduced. As they are decomposed, entries get
deleted — the guard becomes tighter automatically.
"""
from __future__ import annotations

import pathlib

import pytest

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]                # .../src/backend
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"


# ---------------------------------------------------------------------------
# Hard line-count cap. New files must come in under this.
# ---------------------------------------------------------------------------
_MAX_LINES = 1000


# ---------------------------------------------------------------------------
# Allowlist — files that were over the cap on the day this guard
# was introduced. Each entry MUST come off the list once the file is
# decomposed below the threshold.
#
# Paths are relative to ``src/agentclaw/``. Annotated with the line
# count at allowlist time for context — the actual enforcement reads
# the current size on disk.
# ---------------------------------------------------------------------------
_ALLOWLIST: dict[str, str] = {
    # ── bot_management cluster ───────────────────────────────────────
    "adapters/http/bot_management/router.py":
        "~2931 lines — biggest router; bot CRUD + lifecycle + status + engine config + ext.",
    "core/bot_management/services/bot_service.py":
        "~2894 lines — central bot service; handles create/get/update/delete + engine resolution.",
    "core/bot_management/services/data_init_service.py":
        "~1111 lines — bot data initialization service.",

    # ── skill_center cluster ─────────────────────────────────────────
    "core/skill_center/services/skill_service.py":
        "~2444 lines — central skill service; CRUD, market, install, parameters, members.",
    "core/skill_center/services/skill_set_service.py":
        "~2412 lines — skill-set service; default, sync, active management.",
    "core/skill_center/services/git_sync.py":
        "~1012 lines — git-sync orchestration for skills repo.",
    "adapters/http/skill_center/skills.py":
        "~2176 lines — skill router; CRUD + market + members + versions.",
    "adapters/http/skill_center/skillsets.py":
        "~1443 lines — skill-set router; CRUD + skills + mcps + default-skill admin.",

    # ── service_bot cluster ──────────────────────────────────────────
    "core/service_bot/services/publish_flow_service.py":
        "~2222 lines — publish-flow orchestration; staged transitions + retry.",
    "core/service_bot/services/baas_service.py":
        "~2608 lines — single BaasService: BaaS API client + payload builders "
        "+ sandbox/mount-point shaping + dataclasses. Demoted from the Rule 20 "
        "plugin split back to a plain core service (2026-06-13); splitting the "
        "body is a separate refactor.",
    "core/service_bot/services/bot_build_service.py":
        "~1014 lines — bot build orchestration; current code exceeds cap after build config generation.",
    "core/service_bot/services/bot_publish_service.py":
        "~1019 lines — central publish service; rollback logic extracted to publish_rollback_mixin.py.",
    "adapters/http/service_bot/router_publish.py":
        "~1131 lines — publish/upgrade/update-type/restart/offline endpoints.",

    # ── antprocess cluster (vendor facades, prod-only overlay) ───────
    "corp/plugins/prod/antprocess_facades/BPInstanceFacade.py":
        "~2778 lines — BPM instance facade (legacy ANT process integration).",
    "corp/plugins/prod/antprocess_facades/BPInstanceQueryFacade.py":
        "~1397 lines — BPM query facade.",
    "corp/plugins/prod/antprocess_facades/WTTicketActorQueryFacade.py":
        "~1084 lines — WT ticket actor query facade.",

    # ── devices cluster ──────────────────────────────────────────────
    "adapters/http/devices/router.py":
        "~1123 lines — device lifecycle + connection + multi-instance "
        "(instances list / restart / bot_id conn-info) endpoints. Multi-instance "
        "surface (§1/§2/§3) added on top of the existing device CRUD router; "
        "extracting it into a sibling router is a separate refactor.",
    "core/devices/services/device_service.py":
        "~1461 lines — device lifecycle, binding, allocation.",
    "corp/core/devices/services/arca_device_service.py":
        "~1147 lines — arca sandbox device adapter (corp-only prod plugin).",
    "core/desktop_bot/services/desktop_bot_service.py":
        "~1492 lines — desktop-bot lifecycle service.",

    # ── other ────────────────────────────────────────────────────────
    "adapters/http/harness/router.py":
        "~2352 lines — harness router; patch/diagnose/templates/recordings.",
    "plugins/skill_repository.py":
        "~1722 lines — unified ORM skill repository (covers all skill CRUD).",
    "core/bot_public/services/bot_public_service.py":
        "~1166 lines — bot-public / friend-bot discovery + approval service.",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rel(path: pathlib.Path) -> str:
    rel = path.relative_to(_AGENTCLAW_ROOT).as_posix()
    # B11: layers migrate under ``agentclaw/community/<layer>``. Strip the
    # ``community/`` prefix so layer-relative allowlist keys ("core/...") match
    # whichever side of the move a file is on.
    return rel[len("community/"):] if rel.startswith("community/") else rel


def _line_count(path: pathlib.Path) -> int:
    """Physical line count; same metric as ``wc -l``."""
    try:
        return len(path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
def test_no_new_oversized_files():
    """No new file under ``src/agentclaw/`` may exceed ``_MAX_LINES``.

    Existing offenders are tracked in ``_ALLOWLIST``. Anything else
    over the cap is a new R9 violation — split it before merging.
    """
    new_offenders: list[str] = []
    for py in _AGENTCLAW_ROOT.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = _rel(py)
        if rel in _ALLOWLIST:
            continue
        n = _line_count(py)
        if n > _MAX_LINES:
            new_offenders.append(f"{rel}: {n} lines (cap {_MAX_LINES})")

    if new_offenders:
        pytest.fail(
            f"Found {len(new_offenders)} new file(s) over the "
            f"{_MAX_LINES}-line cap. Split before merging — each file "
            "should hold one cohesive concern:\n  "
            + "\n  ".join(new_offenders)
        )


@pytest.mark.unit
def test_allowlist_entries_still_oversized():
    """Allowlist entries must still be over the cap.

    If a file drops below ``_MAX_LINES`` (good — someone split it),
    its allowlist entry becomes stale and must be deleted in the
    same PR. This forces the debt counter to shrink monotonically:
    you cannot leave a "phantom" entry behind once the file is
    refactored.
    """
    # B11: corp-tolerant — a ``corp/`` allowlist entry cannot exist in a corp-absent
    # tree (extracted community repo / dist-builder staged run); skip it there. In the
    # monorepo (corp present) it is still checked.
    corp_present = (_AGENTCLAW_ROOT / "corp").is_dir()
    stale: list[str] = []
    missing: list[str] = []
    for rel in _ALLOWLIST:
        path = _AGENTCLAW_ROOT / rel
        if not path.is_file():
            path = _AGENTCLAW_ROOT / "community" / rel
        if not path.is_file():
            if rel.startswith("corp/") and not corp_present:
                continue
            missing.append(rel)
            continue
        n = _line_count(path)
        if n <= _MAX_LINES:
            stale.append(f"{rel}: now {n} lines (cap {_MAX_LINES})")

    failures: list[str] = []
    if missing:
        failures.append(
            "Allowlist entries no longer point at real files — "
            "delete them:\n  " + "\n  ".join(missing)
        )
    if stale:
        failures.append(
            "Allowlist entries are no longer oversized — delete them so "
            "the debt counter shrinks:\n  " + "\n  ".join(stale)
        )
    if failures:
        pytest.fail("\n\n".join(failures))
