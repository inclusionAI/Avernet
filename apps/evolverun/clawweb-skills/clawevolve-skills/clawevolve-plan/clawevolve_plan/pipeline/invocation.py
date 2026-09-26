from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from ..io import _existing_file_path
from .inputs import _find_plan_path_or_none
from .paths import resolve_run_dir

_SCHEMA_VERSION = "clawevolve.plan-invocation.v2"


def build_invocation_identity(
    args: argparse.Namespace, *, task_id: str
) -> dict[str, Any]:
    """Build the cache identity for every user-controlled Plan input.

    A task id is an output location, not a semantic cache key. Reuse is safe only
    when the selected input source, explicit goal, discovery overrides, target
    boundary, ClawWeb environment, and local/network mode all still match.
    """

    source_arg = str(getattr(args, "plan_source_path", "") or "").strip()
    run_dir = resolve_run_dir(
        str(getattr(args, "run_dir", "") or ""),
        task_id,
        str(getattr(args, "evolve_results_dir", "") or ""),
    )
    if source_arg:
        input_mode = "plan_source"
        source_path = Path(source_arg)
    else:
        diagnose_path = _find_plan_path_or_none(run_dir)
        if diagnose_path is not None:
            input_mode = "diagnose"
            source_path = diagnose_path
        else:
            input_mode = "direct_goal"
            source_path = None

    identity: dict[str, Any] = {
        "schema_version": _SCHEMA_VERSION,
        "task_id": task_id,
        "input_mode": input_mode,
        "goal": str(getattr(args, "goal", "") or "").strip(),
        "source": _file_identity(source_path),
        "discovery_notes": _text_or_file_identity(
            str(getattr(args, "discovery_notes", "") or "")
        ),
        "targets": sorted(
            {
                str(value).strip()
                for value in getattr(args, "target", []) or []
                if str(value).strip()
            }
        ),
        "clawweb_url": str(getattr(args, "clawweb_url", "") or "").rstrip("/"),
        "skip_clawweb_report": bool(
            getattr(args, "skip_clawweb_report", False)
        ),
    }
    canonical = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    identity["fingerprint"] = hashlib.sha256(canonical).hexdigest()
    return identity


def invocation_identity_matches(
    archived: Any, expected: dict[str, Any]
) -> bool:
    if not isinstance(archived, dict):
        return False
    if archived.get("schema_version") != _SCHEMA_VERSION:
        return False
    return str(archived.get("fingerprint") or "") == str(
        expected.get("fingerprint") or ""
    )


def _file_identity(path: Path | None) -> dict[str, str]:
    if path is None:
        return {"path": "", "sha256": ""}
    expanded = path.expanduser()
    return {
        "path": str(expanded.resolve()) if expanded.exists() else str(expanded),
        "sha256": _file_sha256(expanded) if expanded.is_file() else "",
    }


def _text_or_file_identity(value: str) -> dict[str, str]:
    normalized = str(value or "").strip()
    if not normalized:
        return {"kind": "empty", "path": "", "sha256": ""}
    path = _existing_file_path(normalized)
    if path is not None:
        return {
            "kind": "file",
            "path": str(path.resolve()),
            "sha256": _file_sha256(path),
        }
    return {
        "kind": "inline",
        "path": "",
        "sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
