from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from ..io import atomic_write_json, load_json

_STATE_FILE = "plan_generation_state.json"
_SCHEMA_VERSION = "clawevolve.plan-generation.v1"
_REGENERATED_FILES = (
    "objective.md",
    "objective.json",
    "spec-v0.md",
    "spec-v0.json",
    "case_contracts.json",
    "case_contract_audit.json",
    "bench_split.json",
    "clawbench_dataset.zip",
    "clawbench_train_dataset.zip",
    "clawbench_test_dataset.zip",
    "clawbench_manifest.json",
    "clawweb_upload_result.json",
    "clawweb_step_report_result.json",
    "oss_upload_result.json",
    "input_manifest.json",
)
_REGENERATED_DIRS = ("templates", "contract_agent", "document_agent")


def begin_generation(
    output_dir: Path,
    *,
    invocation_identity: dict[str, Any],
) -> None:
    """Invalidate prior derived artifacts before a fresh generation transaction."""

    output_dir.mkdir(parents=True, exist_ok=True)
    for name in _REGENERATED_FILES:
        (output_dir / name).unlink(missing_ok=True)
    for name in _REGENERATED_DIRS:
        path = output_dir / name
        if path.exists():
            shutil.rmtree(path)
    atomic_write_json(
        output_dir / _STATE_FILE,
        {
            "schema_version": _SCHEMA_VERSION,
            "status": "running",
            "invocation_identity": invocation_identity,
        },
    )


def complete_generation(
    output_dir: Path, *, invocation_identity: dict[str, Any]
) -> None:
    atomic_write_json(
        output_dir / _STATE_FILE,
        {
            "schema_version": _SCHEMA_VERSION,
            "status": "complete",
            "invocation_identity": invocation_identity,
        },
    )


def generation_is_complete(
    output_dir: Path, *, expected_identity: dict[str, Any] | None = None
) -> bool:
    path = output_dir / _STATE_FILE
    if not path.is_file():
        return expected_identity is None
    try:
        state = load_json(path)
    except Exception:
        return False
    if not isinstance(state, dict) or state.get("schema_version") != _SCHEMA_VERSION:
        return False
    if state.get("status") != "complete":
        return False
    if expected_identity is None:
        return True
    archived = state.get("invocation_identity")
    return isinstance(archived, dict) and archived.get("fingerprint") == expected_identity.get(
        "fingerprint"
    )
