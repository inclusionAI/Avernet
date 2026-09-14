"""Session I/O: path resolution, file discovery, JSONL reading.

Centralizes all path detection and file discovery for debug scripts.
Handles the path mismatch between:
  - Source code default: ~/.openclaw/logs/clawmind/embedded-sessions/
  - Deployed dist default: ~/.openclaw/logs/workflow-engine/embedded-sessions/
  - WORKFLOW_ENGINE_LOG_DIR env var override

@module session_io
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path


# ---------------------------------------------------------------------------
# OPENCLAW_HOME resolution
# ---------------------------------------------------------------------------

def resolve_openclaw_home() -> str:
    """Derive OPENCLAW_HOME: env var > probe common paths > fallback.

    Priority:
    1. OPENCLAW_HOME environment variable (already set)
    2. Probe common paths for openclaw.json marker file
    3. Fallback to ~/.openclaw
    """
    home = os.environ.get("OPENCLAW_HOME")
    if home:
        return home

    import getpass
    user = getpass.getuser()
    user_home = os.path.expanduser("~")
    candidates = [os.path.join(user_home, ".openclaw")]
    if user != user_home.split("/")[-1]:
        candidates.insert(0, os.path.join("/home", user, ".openclaw"))

    extra = [
        os.path.join(user_home, ".config", "openclaw"),
        "/opt/openclaw",
        "/usr/local/openclaw",
        os.path.join(user_home, "openclaw"),
        os.path.join("/home", user, "openclaw"),
    ]
    for d in extra:
        if d not in candidates:
            candidates.append(d)

    for d in candidates:
        if os.path.isfile(os.path.join(d, "openclaw.json")):
            return d

    return os.path.join(user_home, ".openclaw")


# ---------------------------------------------------------------------------
# Session root path resolution (P3 fix)
# ---------------------------------------------------------------------------

def resolve_session_root(openclaw_home: str | None = None) -> str:
    """Resolve the embedded-sessions root directory.

    Handles the path mismatch between source code and deployed dist by
    checking both possible paths and respecting the WORKFLOW_ENGINE_LOG_DIR
    environment variable.

    After the Jun 2023 refactor (commit 8c20634), the source code default
    changed from 'workflow-engine' to 'clawmind'. When both directories exist
    (common during the transition), we prefer 'clawmind' (the current default).

    Priority:
    1. WORKFLOW_ENGINE_LOG_DIR env var + '/embedded-sessions' (if set)
    2. Probe <openclaw_home>/logs/clawmind/embedded-sessions/ (current default)
    3. Probe <openclaw_home>/logs/workflow-engine/embedded-sessions/ (legacy default)
    4. Fallback: <openclaw_home>/logs/clawmind/embedded-sessions/

    Returns:
        str: Resolved path (may not exist on disk)
    """
    home = openclaw_home or resolve_openclaw_home()

    # 1. Check WORKFLOW_ENGINE_LOG_DIR env var
    env_log_dir = os.environ.get("WORKFLOW_ENGINE_LOG_DIR")
    if env_log_dir:
        return os.path.join(env_log_dir, "embedded-sessions")

    # 2 & 3. Probe both paths
    clawmind_path = os.path.join(home, "logs", "clawmind", "embedded-sessions")
    workflow_engine_path = os.path.join(home, "logs", "workflow-engine", "embedded-sessions")

    clawmind_exists = os.path.isdir(clawmind_path)
    engine_exists = os.path.isdir(workflow_engine_path)

    if clawmind_exists and not engine_exists:
        return clawmind_path
    if engine_exists and not clawmind_exists:
        return workflow_engine_path
    # Both exist or neither exists — prefer clawmind (current default after refactor)
    return clawmind_path


def resolve_engine_log_dir(openclaw_home: str | None = None) -> str:
    """Resolve the engine log directory (containing clawmind-*.jsonl or workflow-engine-*.jsonl).

    After the Jun 2023 refactor (commit 8c20634), the source code default
    changed from 'workflow-engine' to 'clawmind'. When both directories exist
    (common during the transition), we prefer 'clawmind' (the current default).

    Priority:
    1. WORKFLOW_ENGINE_LOG_DIR env var
    2. Probe both clawmind/ and workflow-engine/ paths
    3. Fallback
    """
    home = openclaw_home or resolve_openclaw_home()

    env_log_dir = os.environ.get("WORKFLOW_ENGINE_LOG_DIR")
    if env_log_dir:
        return env_log_dir

    clawmind_path = os.path.join(home, "logs", "clawmind")
    engine_path = os.path.join(home, "logs", "workflow-engine")

    if os.path.isdir(clawmind_path) and not os.path.isdir(engine_path):
        return clawmind_path
    if os.path.isdir(engine_path) and not os.path.isdir(clawmind_path):
        return engine_path
    # Both exist or neither exists — prefer clawmind (current default after refactor)
    return clawmind_path


# ---------------------------------------------------------------------------
# Session directory discovery
# ---------------------------------------------------------------------------

def find_session_dir(
    flow_id: str,
    workflow_id: str | None = None,
    session_root: str | None = None,
) -> str | None:
    """Find a specific flow's embedded session directory.

    If workflow_id is provided, looks directly in <root>/<workflow_id>/<flow_id>/.
    Otherwise, traverses all workflow directories to find the flow_id.

    Args:
        flow_id: Flow execution ID
        workflow_id: Optional workflow ID (avoids directory traversal)
        session_root: Override session root (default: auto-detect)

    Returns:
        Path to the session directory, or None if not found
    """
    root = session_root or resolve_session_root()

    if workflow_id:
        d = os.path.join(root, _safe_path_part(workflow_id), _safe_path_part(flow_id))
        if os.path.isdir(d):
            return d

    # Traverse all workflow directories
    if os.path.isdir(root):
        try:
            for wf_dir in sorted(os.listdir(root)):
                wf_path = os.path.join(root, wf_dir)
                if not os.path.isdir(wf_path):
                    continue
                candidate = os.path.join(wf_path, _safe_path_part(flow_id))
                if os.path.isdir(candidate):
                    return candidate
                # Also try raw flow_id (in case safe_path_part wasn't applied)
                candidate = os.path.join(wf_path, flow_id)
                if os.path.isdir(candidate):
                    return candidate
        except PermissionError:
            pass

    return None


# ---------------------------------------------------------------------------
# Session file discovery (P2 fix — multi-attempt)
# ---------------------------------------------------------------------------

# Pattern to match session files: <nodeId>-attempt-<N>.jsonl
_SESSION_RE = re.compile(r"^(.+)-attempt-(\d+)\.jsonl$")
_COMPACTED_RE = re.compile(r"^(.+)-attempt-(\d+)-compacted\.jsonl$")
_TAIL_RE = re.compile(r"^(.+)-attempt-(\d+)-tail\.jsonl$")
_TRAJECTORY_RE = re.compile(r"^(.+)-attempt-(\d+)\.trajectory\.jsonl$")
_TRAJECTORY_PATH_RE = re.compile(r"^(.+)-attempt-(\d+)\.trajectory-path\.json$")


def discover_session_files(session_dir: str) -> dict[str, dict]:
    """Discover all session files in a directory, grouped by node.

    Returns a dict mapping node_id to a dict with:
        "attempts": [1, 2, ...],           # sorted attempt numbers
        "session_files": {"1": path, ...},  # attempt -> session .jsonl path
        "trajectory_files": {"1": path},    # attempt -> trajectory .jsonl path
        "trajectory_path_files": {"1": path},  # attempt -> trajectory-path .json
        "variants": {"1": ["compacted", "tail"], ...},  # attempt -> variant list

    Args:
        session_dir: Path to the embedded session directory

    Returns:
        Dict of node_id -> file info dict
    """
    nodes: dict[str, dict] = {}

    if not os.path.isdir(session_dir):
        return nodes

    for fname in sorted(os.listdir(session_dir)):
        fpath = os.path.join(session_dir, fname)
        if not os.path.isfile(fpath):
            continue

        # Standard session file: <nodeId>-attempt-<N>.jsonl
        m = _SESSION_RE.match(fname)
        if m:
            node_id, attempt = m.group(1), int(m.group(2))
            _ensure_node(nodes, node_id)
            nodes[node_id]["attempts"].add(attempt)
            nodes[node_id]["session_files"][attempt] = fpath
            continue

        # Compacted variant: <nodeId>-attempt-<N>-compacted.jsonl
        m = _COMPACTED_RE.match(fname)
        if m:
            node_id, attempt = m.group(1), int(m.group(2))
            _ensure_node(nodes, node_id)
            nodes[node_id]["variants"].setdefault(attempt, []).append("compacted")
            continue

        # Tail variant: <nodeId>-attempt-<N>-tail.jsonl
        m = _TAIL_RE.match(fname)
        if m:
            node_id, attempt = m.group(1), int(m.group(2))
            _ensure_node(nodes, node_id)
            nodes[node_id]["variants"].setdefault(attempt, []).append("tail")
            continue

        # Trajectory file: <nodeId>-attempt-<N>.trajectory.jsonl
        m = _TRAJECTORY_RE.match(fname)
        if m:
            node_id, attempt = m.group(1), int(m.group(2))
            _ensure_node(nodes, node_id)
            nodes[node_id]["trajectory_files"][attempt] = fpath
            continue

        # Trajectory path file: <nodeId>-attempt-<N>.trajectory-path.json
        m = _TRAJECTORY_PATH_RE.match(fname)
        if m:
            node_id, attempt = m.group(1), int(m.group(2))
            _ensure_node(nodes, node_id)
            nodes[node_id]["trajectory_path_files"][attempt] = fpath
            continue

    # Sort and finalize
    for node_id in nodes:
        info = nodes[node_id]
        info["attempts"] = sorted(info["attempts"])

    return nodes


def _ensure_node(nodes: dict, node_id: str) -> None:
    """Ensure a node entry exists in the dict."""
    if node_id not in nodes:
        nodes[node_id] = {
            "attempts": set(),
            "session_files": {},
            "trajectory_files": {},
            "trajectory_path_files": {},
            "variants": {},
        }


def select_attempt_files(
    node_info: dict,
    mode: str = "all",
) -> list[tuple[int, str]]:
    """Select which attempt files to analyze based on mode.

    Args:
        node_info: Dict from discover_session_files()[node_id]
        mode: "all" | "last" | "1"

    Returns:
        List of (attempt_number, session_file_path) tuples
    """
    attempts = node_info["attempts"]
    session_files = node_info["session_files"]

    if mode == "all":
        return [(a, session_files[a]) for a in attempts if a in session_files]
    elif mode == "last":
        if not attempts:
            return []
        last = attempts[-1]
        return [(last, session_files[last])] if last in session_files else []
    else:
        # mode == "1" or specific number
        try:
            n = int(mode)
        except ValueError:
            n = 1
        if n in session_files:
            return [(n, session_files[n])]
        return []


# ---------------------------------------------------------------------------
# JSONL reading
# ---------------------------------------------------------------------------

def load_jsonl(path: str) -> list[dict]:
    """Read a JSONL file, returning list of parsed objects.

    Skips blank lines and unparseable lines silently.
    """
    items = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    items.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    pass
    except FileNotFoundError:
        pass
    return items


def iter_jsonl(path: str):
    """Iterate JSONL lines as parsed objects (streaming, lower memory).

    Yields parsed dicts. Skips blank and unparseable lines.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    pass
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# Trajectory file resolution
# ---------------------------------------------------------------------------

def resolve_trajectory_file(session_file: str) -> str | None:
    """Given a session file path, return the corresponding trajectory file path.

    E.g. node-attempt-1.jsonl -> node-attempt-1.trajectory.jsonl

    Returns None if the trajectory file doesn't exist.
    """
    if not session_file.endswith(".jsonl"):
        return None
    traj_path = session_file[:-5] + ".trajectory.jsonl"
    return traj_path if os.path.isfile(traj_path) else None


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _safe_path_part(value: str) -> str:
    """Sanitize a path component (mirrors ClawMind's safePathPart)."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", value)