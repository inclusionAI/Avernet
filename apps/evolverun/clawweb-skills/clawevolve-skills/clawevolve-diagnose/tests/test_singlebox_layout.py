from __future__ import annotations

import json
from pathlib import Path

from clawevolve_diagnose.acquisition.discovery import discover_layout


def test_explicit_profile_uses_configured_workspace_without_global_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    profile = tmp_path / "profile"
    workspace = tmp_path / "workspace"
    profile.mkdir()
    workspace.mkdir()
    (profile / "openclaw.json").write_text(json.dumps({"agents": {"defaults": {"workspace": str(workspace)}}}))
    monkeypatch.chdir(workspace)
    layout = discover_layout(str(profile))
    assert layout["workspace"] == str(workspace)
    assert layout["openclaw_state"] == str(profile)
    assert "/tmp/openclaw" not in layout["doc_dirs"]
    assert str(Path.home() / ".agents/skills") not in layout["skill_dirs"]


def test_legacy_workspace_field_still_takes_precedence(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAWWEB_VERSION", "openversion")
    profile = tmp_path / "profile"
    workspace = tmp_path / "workspace"
    profile.mkdir()
    workspace.mkdir()
    (profile / "openclaw.json").write_text(json.dumps({"workspace": str(workspace), "agents": {"defaults": {"workspace": "/unused"}}}))
    monkeypatch.chdir(workspace)
    assert discover_layout(str(profile))["workspace"] == str(workspace)


def test_internal_explicit_profile_preserves_evidence_directories(tmp_path, monkeypatch):
    monkeypatch.delenv("CLAWWEB_VERSION", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    global_skills = tmp_path / ".agents" / "skills"
    global_skills.mkdir(parents=True)
    profile = tmp_path / "profile"
    (profile / "workspace").mkdir(parents=True)
    (profile / "openclaw.json").write_text(json.dumps({"agents": {"defaults": {"workspace": "/not-an-internal-override"}}}))
    monkeypatch.chdir(tmp_path)
    layout = discover_layout(str(profile))
    assert str(global_skills) in layout["skill_dirs"]
    assert layout["workspace"] == str(profile / "workspace")
