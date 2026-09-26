#!/usr/bin/env python3
"""Remove historical ClawEvolve helper agents and shared-agent sessions."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable


TASK_AGENT_PREFIXES = (
    "clawevolve-diagnose-",
    "clawevolve-plan-discovery-",
    "clawevolve-tune-",
    "clawevolve-review-",
)
SHARED_AGENT_PREFIXES = ("clawbench-report-",)
SHARED_AGENT_IDS = {"clawbench-report"}
LIST_TIMEOUT_SECONDS = 60
DELETE_TIMEOUT_SECONDS = 120
SESSION_DELETE_TIMEOUT_SECONDS = 120
EVOLVE_TASK_MARKER_RE = re.compile(r"(?<![a-z0-9])ev-[a-z0-9][a-z0-9_-]{0,47}", re.IGNORECASE)
EVOLVE_SESSION_LABELS = ("clawevolve", "claw进化", "claw 进化")


def _openclaw_cli_env(openclaw_home: Path) -> dict[str, str]:
    """Pin CLI operations to the Bot's canonical OpenClaw registry."""
    env = dict(os.environ)
    home = str(openclaw_home.parent)
    env["HOME"] = home
    env["OPENCLAW_HOME"] = home
    env["OPENCLAW_STATE_DIR"] = str(openclaw_home)
    env["OPENCLAW_CONFIG_PATH"] = str(openclaw_home / "openclaw.json")
    env.pop("OPENCLAW_PROFILE", None)
    return env


def _agent_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("agents") or payload.get("list") or []
    else:
        rows = []
    return [row for row in rows if isinstance(row, dict)]


def _agent_id(row: dict[str, Any]) -> str:
    return str(row.get("id") or row.get("agentId") or row.get("name") or "").strip()


def _persisted_agent_ids(openclaw_home: Path) -> set[str]:
    config_path = openclaw_home / "openclaw.json"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    agents = payload.get("agents") if isinstance(payload, dict) else None
    rows = agents.get("list") if isinstance(agents, dict) else None
    if not isinstance(rows, list):
        raise ValueError("openclaw.json agents.list is not an array")
    return {
        agent_id.lower()
        for row in rows
        if isinstance(row, dict) and (agent_id := _agent_id(row))
    }


def is_task_scoped_agent(row: dict[str, Any]) -> bool:
    agent_id = _agent_id(row).lower()
    if (
        any(agent_id.startswith(prefix) for prefix in TASK_AGENT_PREFIXES)
        or agent_id.startswith("bench-")
        or agent_id in SHARED_AGENT_IDS
        or any(agent_id.startswith(prefix) for prefix in SHARED_AGENT_PREFIXES)
    ):
        return True
    return False


def has_evolve_task_marker(value: str) -> bool:
    return bool(EVOLVE_TASK_MARKER_RE.search(str(value or "")))


def is_strict_evolve_agent(row: dict[str, Any]) -> bool:
    agent_id = _agent_id(row).lower()
    if not has_evolve_task_marker(agent_id):
        return False
    return any(agent_id.startswith(prefix) for prefix in TASK_AGENT_PREFIXES) or any(
        agent_id.startswith(prefix)
        for prefix in ("bench-", "clawbench-report-")
    )


def is_evolve_agent_for_cleanup(row: dict[str, Any]) -> bool:
    """Match task-marked helpers and all Bench agents on the selected Bot."""
    return is_strict_evolve_agent(row) or is_task_scoped_agent(row)


def _session_key(row: dict[str, Any]) -> str:
    return str(row.get("key") or row.get("sessionKey") or row.get("id") or "").strip()


def _session_agent_id(row: dict[str, Any]) -> str:
    return str(row.get("agentId") or row.get("agent_id") or "").strip()


def is_strict_evolve_main_session(row: dict[str, Any], excluded_markers: tuple[str, ...]) -> bool:
    key = _session_key(row)
    label = str(row.get("label") or row.get("title") or "")
    combined = f"{key} {label}"
    if _session_agent_id(row).lower() not in {"", "main"}:
        return False
    if not has_evolve_task_marker(combined):
        return False
    lowered = combined.lower()
    if not any(marker in lowered for marker in EVOLVE_SESSION_LABELS):
        return False
    return not any(marker and marker.lower() in lowered for marker in excluded_markers)


def _active_agent_ids(
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> set[str] | None:
    try:
        result = run(
            ["ps", "-eo", "args="],
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
    except Exception:  # noqa: BLE001 - unknown activity means skip optional cleanup.
        return None
    active: set[str] = set()
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        parts = line.split()
        if "agent" not in parts or "--agent" not in parts:
            continue
        try:
            active.add(parts[parts.index("--agent") + 1].lower())
        except (ValueError, IndexError):
            continue
    return active


def cleanup_runtime(
    *,
    openclaw_path: str,
    openclaw_home: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    strict_markers: bool = False,
    skip_active_check: bool = False,
    excluded_session_markers: tuple[str, ...] = (),
) -> dict[str, Any]:
    openclaw_env = _openclaw_cli_env(openclaw_home)
    try:
        listed = run(
            [openclaw_path, "agents", "list", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=LIST_TIMEOUT_SECONDS,
            env=openclaw_env,
        )
    except Exception as exc:  # noqa: BLE001 - cleanup is operational hygiene.
        return {
            "status": "degraded",
            "listed": 0,
            "deleted_agents": [],
            "deleted_agent_count": 0,
            "skipped_active_agents": [],
            "deferred_agents": [],
            "failures": [{"stage": "list", "error": str(exc)[-4000:]}],
        }
    if listed.returncode != 0:
        return {
            "status": "degraded",
            "listed": 0,
            "deleted_agents": [],
            "deleted_agent_count": 0,
            "skipped_active_agents": [],
            "deferred_agents": [],
            "failures": [
                {
                    "stage": "list",
                    "error": (listed.stderr or listed.stdout or "unknown error")[-4000:],
                }
            ],
        }
    try:
        rows = _agent_rows(json.loads(listed.stdout or "{}"))
    except json.JSONDecodeError as exc:
        return {
            "status": "degraded",
            "listed": 0,
            "deleted_agents": [],
            "deleted_agent_count": 0,
            "skipped_active_agents": [],
            "deferred_agents": [],
            "failures": [
                {
                    "stage": "list",
                    "error": f"openclaw agents list returned invalid JSON: {exc}",
                }
            ],
        }

    active = set() if skip_active_check else _active_agent_ids(run)
    candidates = [
        row
        for row in rows
        if _agent_id(row)
        and _agent_id(row).lower() != "main"
        and (is_evolve_agent_for_cleanup(row) if strict_markers else is_task_scoped_agent(row))
    ]
    if active is None:
        return {
            "status": "degraded",
            "listed": len(rows),
            "candidate_agent_count": len(candidates),
            "deleted_agents": [],
            "deleted_agent_count": 0,
            "skipped_active_agents": [],
            "deferred_agents": [_agent_id(row) for row in candidates],
            "failures": [
                {
                    "stage": "active-process-check",
                    "error": "unable to determine active OpenClaw agents; cleanup skipped",
                }
            ],
        }
    deleted: list[str] = []
    skipped_active: list[str] = []
    failures: list[dict[str, str]] = []
    deferred: list[str] = []
    delete_errors: dict[str, str] = {}
    for row in candidates:
        agent_id = _agent_id(row)
        if agent_id.lower() in active:
            skipped_active.append(agent_id)
            delete_errors[agent_id.lower()] = "agent is active; deletion skipped"
            continue
        try:
            result = run(
                [openclaw_path, "agents", "delete", agent_id, "--force", "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=DELETE_TIMEOUT_SECONDS,
                env=openclaw_env,
            )
        except Exception as exc:  # noqa: BLE001 - record one target failure and continue.
            delete_errors[agent_id.lower()] = str(exc)[-4000:]
            continue
        error_text = result.stderr or result.stdout or "unknown error"
        if result.returncode != 0:
            delete_errors[agent_id.lower()] = error_text[-4000:]

    if candidates:
        try:
            verified = run(
                [openclaw_path, "agents", "list", "--json"],
                capture_output=True,
                text=True,
                check=False,
                timeout=LIST_TIMEOUT_SECONDS,
                env=openclaw_env,
            )
            if verified.returncode != 0:
                raise RuntimeError(verified.stderr or verified.stdout or "unknown error")
            verified_rows = _agent_rows(json.loads(verified.stdout or "{}"))
            cli_agent_ids = {
                agent_id.lower()
                for row in verified_rows
                if (agent_id := _agent_id(row))
            }
            persisted_agent_ids = _persisted_agent_ids(openclaw_home)
        except Exception as exc:  # noqa: BLE001 - unverified deletion is never success.
            failures.append({"stage": "post-agent-verify", "error": str(exc)[-4000:]})
        else:
            for row in candidates:
                agent_id = _agent_id(row)
                normalized = agent_id.lower()
                if normalized not in cli_agent_ids and normalized not in persisted_agent_ids:
                    deleted.append(agent_id)
                    continue
                detail = delete_errors.get(normalized)
                message = "agent remains registered after delete"
                if detail:
                    message = f"{message}: {detail}"
                failures.append({
                    "stage": "post-agent-verify",
                    "agent_id": agent_id,
                    "error": message[-4000:],
                })
    session_summary: dict[str, Any] = {}
    if strict_markers:
        session_summary = _cleanup_strict_main_sessions(
            openclaw_path=openclaw_path,
            openclaw_home=openclaw_home,
            run=run,
            excluded_markers=excluded_session_markers,
            failures=failures,
            openclaw_env=openclaw_env,
        )

    summary = {
        "status": "ok" if not failures and not skipped_active else "degraded",
        "listed": len(rows),
        "candidate_agent_count": len(candidates),
        "deleted_agents": deleted,
        "deleted_agent_count": len(deleted),
        "skipped_active_agents": skipped_active,
        "deferred_agents": [agent_id for agent_id in deferred if agent_id],
        "failures": failures,
        **session_summary,
    }
    return summary


def _cleanup_strict_main_sessions(
    *,
    openclaw_path: str,
    openclaw_home: Path,
    run: Callable[..., subprocess.CompletedProcess[str]],
    excluded_markers: tuple[str, ...],
    failures: list[dict[str, str]],
    openclaw_env: dict[str, str],
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "listed_session_count": 0,
        "candidate_session_count": 0,
        "deleted_sessions": [],
        "deleted_session_count": 0,
        "skipped_current_session_count": 0,
    }
    session_index = openclaw_home / "agents" / "main" / "sessions" / "sessions.json"
    if not session_index.is_file():
        return summary
    try:
        session_payload = json.loads(session_index.read_text(encoding="utf-8"))
        rows = [
            {"key": key, "agentId": "main", **entry}
            for key, entry in session_payload.items()
            if isinstance(key, str) and isinstance(entry, dict)
        ] if isinstance(session_payload, dict) else []
    except (OSError, json.JSONDecodeError) as exc:
        failures.append({"stage": "session-index", "error": str(exc)[-4000:]})
        return summary
    summary["listed_session_count"] = len(rows)
    candidates = [
        row for row in rows
        if is_strict_evolve_main_session(row, excluded_markers)
    ]
    summary["candidate_session_count"] = len(candidates)
    summary["skipped_current_session_count"] = sum(
        1
        for row in rows
        if _session_agent_id(row).lower() in {"", "main"}
        and has_evolve_task_marker(f"{_session_key(row)} {row.get('label') or ''}")
        and any(marker and marker.lower() in f"{_session_key(row)} {row.get('label') or ''}".lower() for marker in excluded_markers)
    )
    deleted: list[str] = []
    delete_errors: dict[str, str] = {}
    for row in candidates:
        key = _session_key(row)
        params = json.dumps({"key": key}, separators=(",", ":"))
        try:
            result = run(
                [openclaw_path, "gateway", "call", "sessions.delete", "--params", params],
                capture_output=True,
                text=True,
                check=False,
                timeout=SESSION_DELETE_TIMEOUT_SECONDS,
                env=openclaw_env,
            )
        except Exception as exc:  # noqa: BLE001
            delete_errors[key] = str(exc)[-4000:]
            continue
        if result.returncode != 0:
            delete_errors[key] = (result.stderr or result.stdout or "unknown error")[-4000:]
    if candidates:
        try:
            if session_index.exists():
                verified_payload = json.loads(session_index.read_text(encoding="utf-8"))
                if not isinstance(verified_payload, dict):
                    raise ValueError("sessions.json is not an object")
                remaining_keys = {str(key) for key in verified_payload}
            else:
                remaining_keys = set()
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            failures.append({"stage": "post-session-verify", "error": str(exc)[-4000:]})
        else:
            for row in candidates:
                key = _session_key(row)
                if key not in remaining_keys:
                    deleted.append(key)
                    continue
                detail = delete_errors.get(key)
                message = "session remains registered after delete"
                if detail:
                    message = f"{message}: {detail}"
                failures.append({
                    "stage": "post-session-verify",
                    "session_key": key,
                    "error": message[-4000:],
                })
    summary["deleted_sessions"] = deleted
    summary["deleted_session_count"] = len(deleted)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--openclaw-path", default="openclaw")
    parser.add_argument("--openclaw-home", default="/home/admin/.openclaw")
    parser.add_argument("--strict-markers", action="store_true")
    parser.add_argument("--skip-active-check", action="store_true")
    parser.add_argument("--exclude-session-marker", action="append", default=[])
    args = parser.parse_args()
    try:
        result = cleanup_runtime(
            openclaw_path=args.openclaw_path,
            openclaw_home=Path(args.openclaw_home),
            strict_markers=args.strict_markers,
            skip_active_check=args.skip_active_check,
            excluded_session_markers=tuple(args.exclude_session_marker),
        )
    except Exception as exc:  # noqa: BLE001 - hygiene must never block a task.
        result = {
            "status": "degraded",
            "listed": 0,
            "deleted_agents": [],
            "deleted_agent_count": 0,
            "skipped_active_agents": [],
            "deferred_agents": [],
            "failures": [{"stage": "unexpected", "error": str(exc)[-4000:]}],
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
