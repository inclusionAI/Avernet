"""Architecture guard: DIMA must not appear in OSS-shipped layers.

DIMA is an internal product integration; the open-source export must be
DIMA-free. Corp-only DIMA lives under corp/engines/aicoding/ and
corp/plugins/dima/ (export-excluded) and is intentionally NOT scanned here.
"""
from __future__ import annotations

import re
from pathlib import Path

# this file: .../src/engine/src/engine/community/tests/architecture/<f>.py
# parents[2] == .../src/engine/src/engine/community  (the community package root)
_COMMUNITY_PKG = Path(__file__).resolve().parents[2]
# Post-hoist layout: neutral layers (api/core/di/plugin_api) + community impls
# (plugins/engines/local) all live under community/. plugin_api holds the
# vendor-neutral work-item Port Protocol + DTOs and must stay DIMA-free.
# corp impls (corp/plugins/dima, corp/engines/aicoding) are export-excluded
# and intentionally NOT scanned here.
_OSS_DIRS = ["core", "api", "di", "plugin_api", "plugins", "engines"]
_DIMA = re.compile(r"dima", re.IGNORECASE)


def _oss_py_files():
    for d in _OSS_DIRS:
        yield from (_COMMUNITY_PKG / d).rglob("*.py")


def test_no_dima_references_in_oss_layers():
    offenders: list[str] = []
    for f in _oss_py_files():
        if "__pycache__" in f.parts:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _DIMA.search(line):
                offenders.append(f"{f}:{i}: {line.strip()}")
    assert not offenders, "DIMA must not appear in OSS layers:\n" + "\n".join(offenders)
