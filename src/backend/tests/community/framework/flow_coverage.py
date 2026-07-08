# src/backend/tests/framework/flow_coverage.py
"""E2 coverage matrix + E3 exempt list (Plan B).

covered_modules(): union of every FlowCase.covers.
all_core_modules(): the core business modules under agentclaw/core/.
SINGLEBOX_E2E_EXEMPT: modules explicitly NOT yet covered by an e2e flow,
each with a reason. First pass: ALL core modules are exempt (no real flows
yet); Plan C removes one each time a flow covers it. Drain to zero = full
e2e coverage. Exempt = "we explicitly decided not to run this in single box
(yet)", NOT "covered".
"""
from __future__ import annotations

import pkgutil
from pathlib import Path

import agentclaw.community.core as _core_pkg
from tests.community.framework.flow import FlowCase


def covered_modules(flows: list[FlowCase]) -> set[str]:
    out: set[str] = set()
    for f in flows:
        out.update(f.covers)
    return out


def all_core_modules() -> set[str]:
    """Top-level business module package names under agentclaw/core/."""
    regular_packages = {
        mi.name
        for mi in pkgutil.iter_modules(_core_pkg.__path__)
        if mi.ispkg and not mi.name.startswith("_")
    }
    namespace_packages = {
        child.name
        for root in _core_pkg.__path__
        for child in Path(root).iterdir()
        if child.is_dir()
        and not child.name.startswith("_")
        and child.name not in regular_packages
    }
    return regular_packages | namespace_packages


# First pass: all core modules exempt (no real e2e flows exist yet, those
# are Plan C). Each entry: module → reason. Plan C removes one per flow.
#
# This is an EXPLICIT, auditable manifest — NOT derived from
# all_core_modules() at import time. Deriving it would make Task 5's
# covered-or-exempt guard tautological and defeat the drain mechanism.
# Plan C edits this dict by hand, deleting one key per flow it lands.
#
# 这些 key 必须覆盖 all_core_modules() 中尚无真业务流的模块。
_EXEMPT_REASON = "Plan B 基建期：尚无真业务流覆盖，待 Plan C 排水"
_TASK_QUEUE_EXEMPT_REASON = (
    "Internal infra component with no HTTP/router surface (in-process worker + repo); "
    "covered by unit/integration tests, not an e2e flow."
)

SINGLEBOX_E2E_EXEMPT: dict[str, str] = {
    "aicoding": _EXEMPT_REASON,
    # antcode relocated to agentclaw/corp/core (B11 T3.3) — no longer a core module.
    "bot_dormant": _EXEMPT_REASON,
    "approval": _EXEMPT_REASON,
    "auth": _EXEMPT_REASON,
    "bot_public": _EXEMPT_REASON,
    "channel": _EXEMPT_REASON,
    "common_config": _EXEMPT_REASON,
    "config": _EXEMPT_REASON,
    "config_compose": _EXEMPT_REASON,
    "desktop_bot": _EXEMPT_REASON,
    "economy": _EXEMPT_REASON,
    "events": _EXEMPT_REASON,
    "files": _EXEMPT_REASON,
    "group_chat": _EXEMPT_REASON,
    "grt_chat": _EXEMPT_REASON,
    "models": _EXEMPT_REASON,
    "notify": _EXEMPT_REASON,
    "nas_usage": _EXEMPT_REASON,
    "service_bot": _EXEMPT_REASON,
    "services": _EXEMPT_REASON,
    "skill_center": _EXEMPT_REASON,
    "storage": _EXEMPT_REASON,
    "system_config": _EXEMPT_REASON,
    "task_queue": _TASK_QUEUE_EXEMPT_REASON,
    "utils": _EXEMPT_REASON,
    "workspace": _EXEMPT_REASON,
}
