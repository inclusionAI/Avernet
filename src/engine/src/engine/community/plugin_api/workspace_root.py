# Copyright (c) 2004-2026, Ant Group.
# All Rights Reserved.

"""Engine workspace root resolution shared by plugins and core."""
from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger("engine.workspace_root")


def _path_hash(path: Path) -> str:
    # COSEC: workspace paths are sensitive deployment metadata; log only a digest.
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:16]


def workspace_root() -> Path:
    env = os.environ.get("OPENCLAW_WORKSPACE_DIR")
    if env:
        result = Path(env)
        log.info(
            "[workspace_root] configured=true path_hash=%s (per-bot)",
            _path_hash(result),
        )
        return result
    result = Path.home() / ".openclaw" / "workspace"
    log.info("[workspace_root] env=UNSET → %s (fallback,shared)", result)
    return result


def workspace_root_strict() -> Optional[Path]:
    env = os.environ.get("OPENCLAW_WORKSPACE_DIR")
    if env:
        result = Path(env)
        log.debug(
            "[workspace_root_strict] configured=true path_hash=%s",
            _path_hash(result),
        )
        return result
    log.debug("[workspace_root_strict] env=UNSET → None")
    return None
