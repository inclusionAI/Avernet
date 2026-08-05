"""Tests for the gate-and-publish helper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "gate_and_publish_openapi.py"
)
_spec = importlib.util.spec_from_file_location("gate_and_publish_openapi", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
gate = _mod.gate
main = _mod.main

_BCN_ARTIFACT = (
    Path(__file__).resolve().parents[1] / "configs" / "schemas" / "bcn.openapi.json"
)


def _write(path: Path, paths: dict[str, object]) -> None:
    path.write_text(json.dumps({"openapi": "3.1.0", "paths": paths}), encoding="utf-8")


def test_gate_passes_on_additive(tmp_path: Path) -> None:
    published = tmp_path / "pub.json"
    candidate = tmp_path / "cand.json"
    _write(published, {"/openapi/v1/bots": {"get": {}}})
    _write(candidate, {"/openapi/v1/bots": {"get": {}, "post": {}}})
    assert gate(published, candidate, allow_breaking=False) == []


def test_gate_blocks_breaking(tmp_path: Path) -> None:
    published = tmp_path / "pub.json"
    candidate = tmp_path / "cand.json"
    _write(published, {"/openapi/v1/bots": {"get": {}, "post": {}}})
    _write(candidate, {"/openapi/v1/bots": {"get": {}}})
    with pytest.raises(SystemExit) as exc:
        gate(published, candidate, allow_breaking=False)
    assert exc.value.code == 1


def test_allow_breaking_overrides(tmp_path: Path) -> None:
    published = tmp_path / "pub.json"
    candidate = tmp_path / "cand.json"
    _write(published, {"/openapi/v1/bots": {"get": {}, "post": {}}})
    _write(candidate, {"/openapi/v1/bots": {"get": {}}})
    breaks = gate(published, candidate, allow_breaking=True)
    assert breaks  # reported but not blocking


def test_missing_published_treated_as_empty(tmp_path: Path) -> None:
    published = tmp_path / "absent.json"
    candidate = tmp_path / "cand.json"
    _write(candidate, {"/openapi/v1/bots": {"get": {}}})
    assert gate(published, candidate, allow_breaking=False) == []


def test_main_publishes_on_pass(tmp_path: Path) -> None:
    published = tmp_path / "pub.json"
    candidate = tmp_path / "cand.json"
    _write(published, {"/openapi/v1/bots": {"get": {}}})
    _write(candidate, {"/openapi/v1/bots": {"get": {}, "post": {}}})
    assert main([str(published), str(candidate)]) == 0
    assert (
        json.loads(published.read_text())["paths"]
        == json.loads(candidate.read_text())["paths"]
    )


def test_checked_in_bcn_artifact_is_a_32_operation_openapi_document() -> None:
    document = json.loads(_BCN_ARTIFACT.read_text(encoding="utf-8"))
    operations = sum(len(item) for item in document["paths"].values())

    assert document["openapi"] == "3.1.0"
    assert operations == 32
    assert all(
        path.startswith("/openapi/v1/collaboration/") for path in document["paths"]
    )
    assert (
        "post"
        in document["paths"]["/openapi/v1/collaboration/sessions/{session_id}/token"]
    )
    websocket = document["paths"]["/openapi/v1/collaboration/messages/ws"]["get"]
    assert websocket["x-avernet-protocol"] == "websocket"
    assert websocket["x-avernet-security"] == {}
