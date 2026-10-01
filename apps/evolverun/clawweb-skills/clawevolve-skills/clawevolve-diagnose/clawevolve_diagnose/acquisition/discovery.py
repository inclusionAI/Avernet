from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..utils import _path_expand, _read_json_safe


def _resolve_openclaw_state_dir(openclaw_home: str = "") -> Path:
    """Resolve the OpenClaw state root.

    ``scripts/run.sh`` changes into the skill directory before invoking Python,
    while local test commands are usually launched from the workspace root.  For
    relative ``--openclaw-home`` values, prefer the original shell cwd exported
    by the script so local and online commands can share the same entrypoint.
    """

    raw = str(openclaw_home or "").strip()
    if not raw:
        return _path_expand("~/.openclaw")
    path = Path(raw).expanduser()
    if path.is_absolute() or path.exists():
        return path
    invocation_cwd = os.environ.get("CLAWEVOLVE_INVOCATION_CWD", "")
    if invocation_cwd:
        candidate = Path(invocation_cwd).expanduser() / path
        if candidate.exists():
            return candidate
    return path


def _agent_from_path(path: Path) -> str:
    parts = path.resolve().parts
    for i, part in enumerate(parts[:-1]):
        if part == "agents" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


def discover_layout(openclaw_home: str = "") -> dict[str, Any]:
    open_version = os.environ.get("CLAWWEB_VERSION") == "openversion"
    state_dir = _resolve_openclaw_state_dir(openclaw_home)
    cwd = Path.cwd()
    inferred_agent = _agent_from_path(cwd)

    agents: list[Path] = []
    if inferred_agent:
        agents.append(state_dir / "agents" / inferred_agent)
    agents_root = state_dir / "agents"
    if agents_root.exists():
        agents.extend(p for p in agents_root.iterdir() if p.is_dir())
    if not inferred_agent and agents:
        inferred_agent = sorted(
            agents, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True
        )[0].name

    sessions: list[Path] = []
    for agent in agents:
        sessions.extend([agent / "sessions", agent / "workspace" / "sessions"])
    sessions.extend([state_dir / "sessions", cwd / "sessions"])

    workspace = state_dir / "workspace"
    config = (state_dir / "openclaw.json").expanduser()
    cfg = _read_json_safe(config)
    if isinstance(cfg, dict):
        maybe_workspace = (
            cfg.get("workspace") or cfg.get("workspaceDir") or cfg.get("workspace_path")
        )
        if open_version and openclaw_home and not maybe_workspace:
            agents_config = cfg.get("agents")
            defaults = agents_config.get("defaults") if isinstance(agents_config, dict) else None
            maybe_workspace = defaults.get("workspace") if isinstance(defaults, dict) else None
        if maybe_workspace:
            workspace = Path(str(maybe_workspace)).expanduser()

    skill_dirs = [
        workspace / "skills",
        workspace / ".agents" / "skills",
        state_dir / "skills",
    ]
    doc_dirs = [
        workspace / name
        for name in ("docs", "playbooks", "mcp", "tools", "logs", "memory")
    ]
    doc_dirs.extend([state_dir / "logs", state_dir / "skills"])
    # COSEC: an explicit local Bot profile must not collect other users/Bots' global evidence.
    if not open_version:
        skill_dirs.insert(2, _path_expand("~/.agents/skills"))
        doc_dirs.append(Path("/tmp/openclaw"))

    return {
        "openclaw_state": str(state_dir),
        "agent_id": inferred_agent,
        "agents": [str(p) for p in dict.fromkeys(agents)],
        "session_dirs": [str(p) for p in dict.fromkeys(sessions)],
        "workspace": str(workspace),
        "config": str(config) if config.exists() else "",
        "skill_dirs": [str(p) for p in skill_dirs if p.exists()],
        "doc_dirs": [str(p) for p in doc_dirs if p.exists()],
        "self_skill": [str(cwd)],
    }
