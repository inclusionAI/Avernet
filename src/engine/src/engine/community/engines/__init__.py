"""Engine registry loader — the shared base composition entrypoint.

Heterogeneous-engine support: the registry holds multiple engines side-by-side
and the ``EngineManager`` switches the active one by name.

Profile-driven engine set (mutual exclusion on the ``claude_code`` name):
  - **community / test**: OpenClaw + claude_code (community impl, vendored
    18900 relay). The community claude_code engine lives in
    ``community/engines/claude_code/`` and self-registers on import. GitHub
    export ships this set.
  - **corp**: OpenClaw + aicoding + hermes + claude_code (corp impl). The corp
    ``engines/`` packages live under ``corp/engines/`` and are present only in
    the OCB internal checkout (excluded from GitHub export).

Loading claude_code by profile avoids a same-name registration conflict: both
``ClaudeCodeEngine`` (corp) and ``ClaudeCodeCommunityEngine`` (community)
register under ``"claude_code"``, so exactly one must load per process.

This module is the community base loader; ``corp`` engines are pulled in here
via branch-local imports gated on ``ENGINE_PROFILE``.
"""
from __future__ import annotations

import importlib
import logging

from engine.community.di.profile import EngineProfile

log = logging.getLogger(__name__)


def _optional_import_module(module_name: str, *, label: str) -> None:
    try:
        importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            log.debug("optional engine package not present: %s", label)
            return
        raise


# OpenClaw is the public/community engine and must always be importable.
_optional_import_module("engine.community.engines.openclaw", label="openclaw")

_profile = EngineProfile.detect()

# Profile-gated engine set. corp loads the corp-only engines (aicoding / hermes /
# corp claude_code); community/test load only the open-source community
# claude_code engine. This gating is load-bearing: importing the engines package
# must NOT pull corp-only engines into a community app. claude_code is
# additionally mutually exclusive by profile to avoid the same-name registry
# conflict (ClaudeCodeEngine corp vs ClaudeCodeCommunityEngine community).
if _profile is EngineProfile.CORP:
    _optional_import_module("engine.corp.engines.claude_code", label="claude_code (corp)")
    # Other internal legacy engines: present in OCB, excluded from GitHub export.
    for _name in ("aicoding", "hermes"):
        _optional_import_module(f"engine.corp.engines.{_name}", label=_name)
else:  # community / test
    # Community claude_code engine: composition root under
    # community/engines/claude_code/; self-registers on import.
    # corp engines/claude_code/ + aicoding/hermes stay unloaded.
    _optional_import_module("engine.community.engines.claude_code", label="claude_code (community)")