"""The legacy activation writers stay dead.

``SkillSetActivator`` and ``SkillSetSwitcher`` wrote ``ac_skill_set.is_active``
and synced symlinks imperatively while never touching Installation — the
two-writers defect the Installation-single-source-of-truth refactor removed
(specs/2026-08-24-installation-single-source-of-truth). Every activation now
flows through the desired-state UoW and the runtime projection. A returning
reference is a returning second writer, so this guard fails on the *name*,
anywhere in the backend source tree.
"""

from __future__ import annotations

import pathlib

_THIS_FILE = pathlib.Path(__file__).resolve()
_BACKEND_ROOT = _THIS_FILE.parents[3]
_AGENTCLAW_ROOT = _BACKEND_ROOT / "src" / "agentclaw"

_FORBIDDEN = ("SkillSetActivator", "SkillSetSwitcher")


def test_no_reference_to_the_legacy_activation_writers():
    violations: list[str] = []
    for path in sorted(_AGENTCLAW_ROOT.rglob("*")):
        if path.suffix not in {".py", ".md"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for name in _FORBIDDEN:
            if name in text:
                violations.append(
                    f"{path.relative_to(_AGENTCLAW_ROOT)}: mentions {name}"
                )
    assert not violations, (
        "Legacy activation writers referenced again — activation state has "
        "exactly one writer, the desired-state UoW:\n" + "\n".join(violations)
    )
