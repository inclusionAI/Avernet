#!/usr/bin/env python3
"""
workflow_flow.py — 多源采集 Workflow 主流程/流转数据

从本地文件（engine.db, registry.sqlite, ClawMind JSONL 日志, per-node session JSONL,
workflow YAML）收集 workflow 级元数据、DAG 结构、节点执行状态与输出、事件时间线。

导出 `build_workflow_trace(flow_id, workflow_id, oc_home, session_dir) -> dict`。
全链路逐源 try/except，任何一项缺失只缺字段、不抛出。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# 1. Registry.sqlite — workflow 级元数据
# ---------------------------------------------------------------------------

def _query_flow_registry(oc_home: str, flow_id: str) -> dict | None:
    """Query ``<oc_home>/flows/registry.sqlite`` for flow-level metadata."""
    db_path = os.path.join(oc_home, "flows", "registry.sqlite")
    if not os.path.isfile(db_path):
        return None
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT flow_id, status, goal, current_step, created_at, updated_at, ended_at, state_json "
            "FROM flow_runs WHERE flow_id = ?",
            (flow_id,),
        ).fetchone()
        db.close()
        if not row:
            return None
        return {
            "flow_id": row["flow_id"],
            "status": row["status"],
            "goal": row["goal"],
            "current_step": row["current_step"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "ended_at": row["ended_at"],
            "state_json": row["state_json"],
        }
    except Exception:
        return None


def _iso_duration_s(started_at: Any, ended_at: Any) -> float | None:
    """Compute duration in seconds from two ISO-8601 timestamps; None on failure."""
    if not started_at or not ended_at:
        return None
    try:
        s = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(ended_at).replace("Z", "+00:00"))
        return round((e - s).total_seconds(), 1)
    except Exception:
        return None


def _unwrap_json(value: Any) -> Any:
    """Decode a JSON-string-in-JSON value into its native object, if applicable.

    Registry ``state_json`` is double-encoded, and nested fields (notably
    ``nodeStates.<id>.result`` — a node's output object) can be stored as a
    JSON *string* rather than a dict. Returning such a ``str`` as ``output``
    makes ``get_trace_node_output(...)`` hand back a wrapped object instead of
    a dict, so callers' ``.get(...)`` or key access blows up / scores 0.
    Unwrap one level when the value is a str that parses to a non-str (dict/list).
    A str that fails to parse — or parses to another str — is left as-is.
    """
    if not isinstance(value, str):
        return value
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return value
    return decoded if not isinstance(decoded, str) else value


def _normalize_executor(value: Any) -> str:
    """Normalize executor from state_json — may be a dict ({type,command,args,env}) or str."""
    if isinstance(value, dict):
        return str(value.get("type") or value.get("name") or value.get("executor") or "")
    if value is None:
        return ""
    return str(value)


def _parse_state_json(state_json_raw: Any) -> dict:
    """Parse ``flow_runs.state_json`` into trace data (the primary source).

    ``state_json`` is the workflow-engine's authoritative runtime snapshot. It
    carries per-node status/result/usage, the full DAG (``workflowSnapshot``),
    audit timeline, and final ``workflowData.outputs``. This is more complete
    than the engine-log event stream (which only emits node_started/succeeded
    and rarely stashes outputs) and far more reliable than searching for a YAML
    file on disk.

    Field paths are best-effort: every access is guarded, and a missing/NaN
    sub-structure merely leaves a field empty rather than raising.

    ``flow_runs.state_json`` is **double-encoded** in the registry: the TEXT column
    stores a JSON string that itself wraps a JSON document, i.e. ``"{\\"nodeStates\\"...}"``
    — a JSON-quoted wrapper around the real object. A single ``json.loads`` yields a
    *str* (``{"nodeStates"...}``) rather than a dict, so ``state.get("nodeStates")``
    silently returns nothing and ``node_executions`` ends up empty → all
    ``get_trace_node_output`` calls miss → PI/SC/FN boards score 0. We unwrap
    repeatedly ( guarding triple-encoding on principle) while the result is still a
    JSON-parseable str, bounded so we never loop on a non-JSON str value.

    Returns:
        ``{node_executions, dag, usage, audit_log, workflow_outputs}``
    """
    out: dict[str, Any] = {
        "node_executions": [],
        "dag": [],
        "usage": {},
        "audit_log": [],
        "workflow_outputs": {},
    }
    state: Any = state_json_raw
    # Unwrap JSON-string-in-JSON encoding (registry columns are double-encoded).
    # Re-parse for as long as we keep getting a str back (handles double/triple),
    # capped so a non-JSON str value can't spin.
    for _ in range(8):
        if not isinstance(state, str):
            break
        try:
            state = json.loads(state)
        except (json.JSONDecodeError, TypeError, ValueError):
            return out  # left as a non-JSON str — treat as empty
    if not isinstance(state, dict):
        return out

    # ── nodeStates.<nodeId> → node_executions ──
    node_states = state.get("nodeStates") or {}
    if isinstance(node_states, dict):
        for nid, ns in node_states.items():
            if not isinstance(ns, dict):
                continue
            started = ns.get("startedAt") or ns.get("started_at")
            ended = (
                ns.get("completedAt")
                or ns.get("completed_at")
                or ns.get("endedAt")
                or ns.get("ended_at")
            )
            result = _unwrap_json(ns.get("result"))
            # ClawMind may store childSessionKey inside result (not at nodeStates
            # top level) — pre-extract so it's available for session_id below.
            _cs_key = (result.get("childSessionKey", "") if isinstance(result, dict) else "")
            out["node_executions"].append({
                "node_id": nid,
                "executor_type": _normalize_executor(ns.get("executor") or ns.get("executor_type")),
                "status": ns.get("status", ""),
                "started_at": started,
                "ended_at": ended,
                "duration_s": _iso_duration_s(started, ended),
                "attempt": ns.get("attempt", 1),
                "error": ns.get("error") or None,
                # outputKeys/resultKeys both seen in the wild; normalize to a list
                "output_keys": list(
                    ns.get("outputKeys")
                    or ns.get("resultKeys")
                    or ns.get("output_keys")
                    or []
                ),
                # nodeStates.<id>.result is the full node output JSON — the key
                # win behind switching to state_json as the primary source.
                "output": result if result is not None else None,
                "usage": ns.get("usage") if isinstance(ns.get("usage"), dict) else {},
                # childSessionKey lets us locate a subagent's independent session
                # JSONL later (see _resolve_subagent_session_paths strategy0).
                # Also check result.childSessionKey (ClawMind stores it there).
                "session_id": (
                    ns.get("childSessionKey")
                    or ns.get("session_id")
                    or ns.get("sessionId")
                    or _cs_key
                    or ""
                ),
            })

    # ── workflowSnapshot.nodes[] → dag (runtime snapshot, beats YAML file search) ──
    snap = state.get("workflowSnapshot") or {}
    snap_nodes = snap.get("nodes") if isinstance(snap, dict) else None
    if isinstance(snap_nodes, list):
        for n in snap_nodes:
            if not isinstance(n, dict) or not n.get("id"):
                continue
            deps = n.get("dependsOn") or []
            if isinstance(deps, str):
                deps = [deps]
            out["dag"].append({
                "node": n["id"],
                "executor": _normalize_executor(n.get("executor") or n.get("type")),
                "deps": list(deps),
                "title": n.get("title") or n.get("description") or "",
                "skill_name": n.get("skillName") or n.get("skill") or "",
                "runtime_config": n.get("runtimeConfig") or {},
            })

    # ── Workflow-level usage / audit / outputs ──
    if isinstance(state.get("usage"), dict):
        out["usage"] = state["usage"]
    if isinstance(state.get("auditLog"), list):
        out["audit_log"] = state["auditLog"]
    wf_data = _unwrap_json(state.get("workflowData"))
    if isinstance(wf_data, dict):
        out["workflow_outputs"] = _unwrap_json(wf_data.get("outputs")) or {}

    return out


def _merge_executions(primary: list[dict], secondary: list[dict]) -> list[dict]:
    """Merge per-node execution records by node_id; *primary* wins field-level.

    Used to fold engine-log-derived node stats into the state_json-derived
    ``primary`` list: state_json provides the authoritative output/status, while
    the engine log supplies duration/executor/session_id when state_json omitted
    them. Nodes only present in *secondary* are appended.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for entry in primary:
        nid = entry.get("node_id", "")
        if not nid:
            continue
        by_id[nid] = dict(entry)
        order.append(nid)
    for entry in secondary:
        nid = entry.get("node_id", "")
        if not nid:
            continue
        if nid not in by_id:
            by_id[nid] = dict(entry)
            order.append(nid)
            continue
        target = by_id[nid]
        for k, v in entry.items():
            if k in ("node_id",):
                continue
            cur = target.get(k)
            # Only fill when primary's value is empty/None — never overwrite
            # state_json's authoritative output/status with engine-log data.
            if cur in (None, "", [], {}) and v not in (None, "", [], {}):
                target[k] = v
    return [by_id[nid] for nid in order]


# ---------------------------------------------------------------------------
# 2. ClawMind JSONL 日志 — 节点执行事件
# ---------------------------------------------------------------------------

def _resolve_engine_log_dir(oc_home: str) -> str:
    """Resolve the engine log directory."""
    candidate = os.path.join(oc_home, "logs", "clawmind")
    if os.path.isdir(candidate):
        return candidate
    return os.path.join(oc_home, "logs", "workflow-engine")


def _parse_engine_log_events(flow_id: str, log_path: str) -> list[dict]:
    """Parse a single engine log JSONL file for ALL events matching *flow_id*.

    Captures the full event stream (not just node_started/succeeded/failed) so that
    inter-node data passing, engine decisions, and workflow lifecycle events are
    all available for trace building.
    """
    events = []
    try:
        with open(log_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                if d.get("flow_id") != flow_id:
                    continue
                et = d.get("event_type", "")
                det = d.get("details", {})
                data = det.get("data", {}) if isinstance(det, dict) else {}

                # Build a rich event record
                # NOTE: node_id lives inside details, not at event top-level.
                # executor_type may be at top-level or inside details — check both.
                node_id = (
                    det.get("node_id", "") if isinstance(det, dict) else ""
                ) or d.get("node_id", "") or d.get("node", "")
                executor = (
                    d.get("executor_type", "") or d.get("executor", "")
                    or (det.get("executor_type", "") if isinstance(det, dict) else "")
                    or (det.get("executor", "") if isinstance(det, dict) else "")
                )
                # Capture session_id for subagent/agent nodes (so we can find
                # their independent session JSONL files later).
                session_id = ""
                if isinstance(det, dict):
                    session_id = (
                        det.get("session_id", "")
                        or det.get("sessionId", "")
                        or det.get("agent_session_id", "")
                        or det.get("agentSessionId", "")
                        or data.get("session_id", "")
                        or data.get("sessionId", "")
                    )

                evt: dict[str, Any] = {
                    "time": d.get("time", ""),
                    "event": et,
                    "node": node_id,
                    "executor": executor,
                    "session_id": session_id,
                }

                # Node lifecycle events — extract result/output data
                if et in ("node_succeeded", "node_failed"):
                    evt["error"] = det.get("error") if isinstance(det, dict) else None
                    # resultKeys
                    result_keys = data.get("resultKeys", data.get("result_keys", []))
                    if isinstance(result_keys, list):
                        evt["result_keys"] = result_keys
                    # Aggressively scan data for output values beyond resultKeys.
                    # Engines often stash the full node result in fields like
                    # "output", "result", "response", or directly in data itself
                    # (when data is a flat dict of key->value).
                    output_candidate: dict[str, Any] = {}
                    skip_keys = {"resultKeys", "result_keys", "nodeId", "node_id",
                                 "flowId", "flow_id", "workflowId", "workflow_id"}
                    for k, v in data.items():
                        if k in skip_keys:
                            continue
                        if isinstance(v, (str, int, float, bool, list, dict)):
                            output_candidate[k] = v
                    if output_candidate:
                        evt["output_data"] = output_candidate

                # Workflow lifecycle events
                if et in ("workflow_started", "workflow_finished"):
                    evt["status"] = d.get("status", "")
                    if isinstance(det, dict):
                        evt["goal"] = det.get("goal", det.get("description", ""))

                events.append(evt)
    except Exception:
        pass
    return events


def _build_node_executions_from_logs(flow_id: str, oc_home: str) -> tuple[list[dict], list[dict]]:
    """Scan engine logs for node execution events and build per-node stats + timeline.

    Returns:
        (node_executions: list[dict], timeline: list[dict])
    """
    log_dir = _resolve_engine_log_dir(oc_home)
    raw_events = []

    # Scan last 5 days of logs
    for delta in range(5):
        d = datetime.now() - timedelta(days=delta)
        for prefix in ("clawmind", "workflow-engine"):
            log_path = os.path.join(log_dir, f"{prefix}-{d.strftime('%Y-%m-%d')}.jsonl")
            if os.path.isfile(log_path):
                raw_events.extend(_parse_engine_log_events(flow_id, log_path))
                break
        if raw_events:
            break

    if not raw_events:
        return [], []

    # Build per-node stats + collect workflow-level metadata
    node_map: dict[str, dict] = {}
    wf_started_at = None
    wf_ended_at = None
    wf_status = None

    for e in raw_events:
        et = e.get("event", "")

        # ── Workflow lifecycle events ──
        if et == "workflow_started":
            wf_started_at = e.get("time")
            wf_status = "running"
            continue
        if et == "workflow_finished":
            wf_ended_at = e.get("time")
            wf_status = e.get("status", "succeeded")
            continue

        # ── Node events ──
        nid = e.get("node", "")
        if not nid:
            continue
        if nid not in node_map:
            node_map[nid] = {
                "node_id": nid,
                "executor_type": e.get("executor", ""),
                "started_at": None,
                "ended_at": None,
                "duration_s": None,
                "status": None,
                "attempt": 1,
                "error": None,
                "output_keys": [],
                "output": None,
                "session_id": e.get("session_id", ""),
            }
        entry = node_map[nid]
        if et == "node_started":
            entry["started_at"] = e.get("time")
            entry["executor_type"] = e.get("executor") or entry["executor_type"]
            if e.get("session_id") and not entry.get("session_id"):
                entry["session_id"] = e["session_id"]
        elif et in ("node_succeeded", "node_failed"):
            entry["ended_at"] = e.get("time")
            entry["status"] = "succeeded" if et == "node_succeeded" else "failed"
            entry["error"] = e.get("error")
            if e.get("result_keys"):
                entry["output_keys"] = e["result_keys"]
            # Extract output data from the stashed details.data fields
            output_data = e.get("output_data")
            if output_data:
                # If output is already a dict with meaningful keys, use it directly
                if entry["output"] is None:
                    entry["output"] = output_data
                elif isinstance(entry["output"], dict):
                    entry["output"].update(output_data)

    # Calculate durations
    for nid, entry in node_map.items():
        if entry["started_at"] and entry["ended_at"]:
            try:
                start = datetime.fromisoformat(entry["started_at"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(entry["ended_at"].replace("Z", "+00:00"))
                entry["duration_s"] = round((end - start).total_seconds(), 1)
            except Exception:
                pass

    node_executions = sorted(node_map.values(), key=lambda x: x["started_at"] or "")

    # Timeline: ALL events (including workflow lifecycle, not just node events)
    timeline = [
        {
            "event_type": e.get("event", "?"),
            "node": e.get("node", ""),
            "time": e.get("time", ""),
        }
        for e in raw_events
    ]
    timeline.sort(key=lambda x: x["time"])

    # Workflow-level metadata extracted from engine log
    wf_meta = {
        "started_at": wf_started_at,
        "ended_at": wf_ended_at,
        "status": wf_status,
    }

    return node_executions, timeline, wf_meta


# ---------------------------------------------------------------------------
# 3. engine.db — 节点级输出探测
# ---------------------------------------------------------------------------

def _probe_engine_db(oc_home: str, flow_id: str) -> dict[str, dict] | None:
    """Probe engine.db for node-level result/output data.

    Schema is discovered at runtime (no hardcoded column names).
    Returns {node_id: {output, result, ...}} or None.
    """
    db_path = os.path.join(oc_home, "workflow", "engine.db")
    if not os.path.isfile(db_path):
        return None

    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row

        # Discover schema
        tables = [
            row[0] for row in
            db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        ]

        # Look for node-execution related tables
        node_tables = [t for t in tables if "node" in t.lower() or "execution" in t.lower()]
        if not node_tables:
            db.close()
            return None

        result: dict[str, dict] = {}
        for tbl in node_tables:
            try:
                cols = [row[1] for row in db.execute(f"PRAGMA table_info({tbl})").fetchall()]
            except Exception:
                continue

            # Check if this table has flow_id + node_id columns
            has_flow = any(c in cols for c in ("flow_id", "flowId"))
            has_node = any(c in cols for c in ("node_id", "nodeId", "node_name"))
            if not (has_flow and has_node):
                continue

            flow_col = next(c for c in cols if c in ("flow_id", "flowId"))
            node_col = next(c for c in cols if c in ("node_id", "nodeId", "node_name"))

            try:
                rows = db.execute(
                    f"SELECT * FROM {tbl} WHERE {flow_col} = ?", (flow_id,)
                ).fetchall()
            except Exception:
                continue

            for row in rows:
                row_dict = dict(row)
                node_id = row_dict.get(node_col, "")
                if not node_id:
                    continue
                if node_id not in result:
                    result[node_id] = {}
                # Collect output/result columns
                for col in cols:
                    if col in (flow_col, node_col):
                        continue
                    if "output" in col.lower() or "result" in col.lower() or "data" in col.lower():
                        val = row_dict.get(col)
                        if val is not None:
                            result[node_id][col] = val

        db.close()
        return result if result else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 4. Per-node session JSONL — agent 节点最终输出
# ---------------------------------------------------------------------------

def _extract_node_output_from_session(session_path: str) -> dict | None:
    """Extract the final JSON output from a per-node session JSONL file.

    Finds the last assistant message with text content, then attempts to parse
    it as JSON (raw or ```json```-wrapped). Returns None if unparseable.
    """
    final_text = ""
    try:
        with open(session_path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    obj = json.loads(line.strip())
                except (json.JSONDecodeError, ValueError):
                    continue
                msg = obj.get("message", {})
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text" and c.get("text", "").strip():
                        final_text = c["text"].strip()
    except Exception:
        return None

    if not final_text:
        return None

    # Try raw JSON parse
    try:
        return json.loads(final_text)
    except (json.JSONDecodeError, ValueError):
        pass

    # Try ```json``` wrapped
    m = re.search(r"```json\s*(\{.*?\})\s*```", final_text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _collect_agent_outputs(
    session_dir: str,
    node_executions: list[dict],
    subagent_sessions: dict[str, str] | None = None,
) -> None:
    """Mutate *node_executions* in-place: fill ``output`` for agent-type nodes.

    Complements ``state_json.nodeStates.<id>.result`` (set in step 1). A node
    whose ``output`` is already present is left untouched — state_json is the
    authoritative source and wins. For the rest:

      - ``embedded-agent`` nodes read ``<session_dir>/<nid>-attempt-1.jsonl``.
      - ``subagent`` nodes read the externally-resolved session JSONL from
        ``subagent_sessions`` (their sessions live outside ``session_dir``).

    Subagent output is therefore now reachable (previously this function only
    scanned ``session_dir``, where subagent files never exist).
    """
    sa = subagent_sessions or {}
    for entry in node_executions:
        nid = entry.get("node_id", "")
        if not nid:
            continue
        # state_json already gave us an authoritative output — never overwrite.
        if entry.get("output") is not None:
            continue
        # Only agent-type nodes have a session JSONL to mine (skip cli-script/done)
        exe = entry.get("executor_type", "").lower()
        if not exe or exe in ("cli-script", "done"):
            continue

        # Subagent nodes run in independent sessions resolved by
        # _resolve_subagent_session_paths; embedded-agent nodes stay in
        # the workflow's embedded-sessions directory.
        if "subagent" in exe and nid in sa:
            session_path = sa[nid]
        else:
            session_path = os.path.join(session_dir, f"{nid}-attempt-1.jsonl")

        if not session_path or not os.path.isfile(session_path):
            continue

        try:
            output = _extract_node_output_from_session(session_path)
            if output:
                entry["output"] = output
        except Exception:
            continue


# ---------------------------------------------------------------------------
# 4b. Subagent session discovery — resolve independent session JSONL paths
# ---------------------------------------------------------------------------

def _resolve_subagent_session_paths(
    oc_home: str,
    node_executions: list[dict],
) -> dict[str, str]:
    """Find subagent session JSONL files outside the workflow session directory.

    Subagent nodes run in **independent** agent sessions whose JSONL files live
    under ``~/.openclaw/agents/<agent_id>/sessions/<session_id>.jsonl`` (or as a
    directory), NOT in the workflow's ``embedded-sessions/`` tree.

    Strategy (tried in order):
      0. Use ``childSessionKey`` (from state_json, carried in session_id) to
         locate the child session directly — most precise.
      1. Use ``session_id`` captured from engine log events.
      2. Scan agent session stores for sessions created within the workflow time
         window (fallback when session_id is missing from logs).

    Returns:
        ``{node_id: external_session_jsonl_path}`` for nodes whose session files
        were found outside the workflow directory.
    """
    result: dict[str, str] = {}

    # Collect timing bounds from node_executions
    wf_start: str | None = None
    wf_end: str | None = None
    for entry in node_executions:
        t = entry.get("started_at") or entry.get("ended_at")
        if t:
            if wf_start is None or t < wf_start:
                wf_start = t
            if wf_end is None or t > wf_end:
                wf_end = t

    # Parse into datetime for time-range fallback (with generous padding)
    ts_start: Any = None
    ts_end: Any = None
    if wf_start and wf_end:
        try:
            ts_start = datetime.fromisoformat(wf_start.replace("Z", "+00:00")) - timedelta(minutes=5)
            ts_end = datetime.fromisoformat(wf_end.replace("Z", "+00:00")) + timedelta(minutes=5)
        except Exception:
            ts_start = ts_end = None

    for entry in node_executions:
        nid = entry.get("node_id", "")
        exe = entry.get("executor_type", "").lower()
        if not nid:
            continue
        # Only subagent / agent nodes have independent sessions
        if "subagent" not in exe and "agent" not in exe:
            continue

        session_id = entry.get("session_id", "")
        found_path: str | None = None

        # ── Strategy 0: childSessionKey lookup (most precise) ──
        # state_json exposes each subagent node's childSessionKey (filled into
        # session_id in step 1). It pinpoints the child session directly,
        # avoiding the noisy time-window fallback when the engine log didn't
        # also echo a plain session id.
        if session_id and (
            "child" in session_id or (":" in session_id and nid in session_id)
        ):
            found_path = _find_session_jsonl_by_child_key(oc_home, session_id, nid)

        # ── Strategy 1: Direct session_id lookup ──
        if not found_path and session_id:
            found_path = _find_session_jsonl_by_id(oc_home, session_id)

        # ── Strategy 2: Time-range scan ──
        if not found_path and ts_start and ts_end:
            found_path = _find_session_jsonl_by_time(
                oc_home, nid, ts_start, ts_end
            )

        if found_path:
            result[nid] = found_path

    return result


def _find_session_jsonl_by_id(oc_home: str, session_id: str) -> str | None:
    """Search agent session directories for a session JSONL by ID.

    Looks in ``<oc_home>/agents/*/sessions/`` for:
      - ``<session_id>.jsonl``
      - ``<session_id>/`` directory containing ``*-attempt-*.jsonl``
    """
    agents_dir = os.path.join(oc_home, "agents")
    if not os.path.isdir(agents_dir):
        return None

    for agent_name in os.listdir(agents_dir):
        sessions_dir = os.path.join(agents_dir, agent_name, "sessions")
        if not os.path.isdir(sessions_dir):
            continue

        # Try direct .jsonl file
        direct = os.path.join(sessions_dir, f"{session_id}.jsonl")
        if os.path.isfile(direct):
            return direct

        # Try directory with attempt files
        session_subdir = os.path.join(sessions_dir, session_id)
        if os.path.isdir(session_subdir):
            for fname in sorted(os.listdir(session_subdir)):
                if "attempt" in fname and fname.endswith(".jsonl"):
                    return os.path.join(session_subdir, fname)

    return None


_CHILD_INDEX_CACHE: dict | None = None   # process-level cache for sessions.json


def _load_child_index(oc_home: str) -> dict:
    """Load and merge all sessions.json files under oc_home (process-cached).

    Scans ``agents/*/sessions/sessions.json`` and top-level ``sessions.json``.
    Returns a merged dict mapping session keys → session info dicts.
    """
    global _CHILD_INDEX_CACHE
    if _CHILD_INDEX_CACHE is not None:
        return _CHILD_INDEX_CACHE

    merged: dict = {}
    candidates: list[str] = []

    agents_dir = os.path.join(oc_home, "agents")
    if os.path.isdir(agents_dir):
        for a in os.listdir(agents_dir):
            p = os.path.join(agents_dir, a, "sessions", "sessions.json")
            if os.path.isfile(p):
                candidates.append(p)

    top_sj = os.path.join(oc_home, "sessions.json")
    if os.path.isfile(top_sj):
        candidates.append(top_sj)

    for p in candidates:
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                merged.update(data)
        except (json.JSONDecodeError, OSError, ValueError):
            continue

    _CHILD_INDEX_CACHE = merged
    return merged


def _resolve_child_session_via_index(
    oc_home: str, child_key: str
) -> str | None:
    """Resolve childSessionKey to .jsonl path via sessions.json index.

    sessions.json key format: ``agent:<agentId>:child:<node>:<rand>:<unix-ms>``
    childSessionKey format:    ``child:<node>:<rand>:<unix-ms>``

    We do prefix-agnostic matching: any index key ending with child_key is a hit.
    """
    if not child_key:
        return None

    index = _load_child_index(oc_home)
    if not isinstance(index, dict) or not index:
        return None

    # Find matching entry (prefix-agnostic: key ends with child_key)
    target = None
    for k, v in index.items():
        if isinstance(k, str) and (k == child_key or k.endswith(":" + child_key)):
            target = v
            break
    if target is None:
        return None

    # Extract UUID from entry value
    session_uuid = ""
    if isinstance(target, str):
        session_uuid = target
    elif isinstance(target, dict):
        session_uuid = (
            target.get("sessionId", "")
            or target.get("session_id", "")
            or target.get("id", "")
            or target.get("uuid", "")
            or ""
        )
    if not session_uuid:
        return None

    # If value is already a .jsonl path, use directly
    if session_uuid.endswith(".jsonl") and os.path.isfile(session_uuid):
        return session_uuid

    # Locate <uuid>.jsonl under agents/*/sessions/ or sessions/
    search_roots: list[str] = []
    agents_dir = os.path.join(oc_home, "agents")
    if os.path.isdir(agents_dir):
        for a in os.listdir(agents_dir):
            sd = os.path.join(agents_dir, a, "sessions")
            if os.path.isdir(sd):
                search_roots.append(sd)
    top = os.path.join(oc_home, "sessions")
    if os.path.isdir(top):
        search_roots.append(top)

    for sd in search_roots:
        direct = os.path.join(sd, f"{session_uuid}.jsonl")
        if os.path.isfile(direct):
            return direct
        # Also try directory with attempt files
        if os.path.isdir(os.path.join(sd, session_uuid)):
            hit = _first_attempt_jsonl(os.path.join(sd, session_uuid))
            if hit:
                return hit
    return None


def _find_session_jsonl_by_child_key(
    oc_home: str, child_key: str, node_id: str
) -> str | None:
    """Locate a subagent's independent session JSONL by its ``childSessionKey``.

    ``childSessionKey`` looks like ``child:<node-id>:<rand>:<unix-ms>``. The
    child session is recorded by the engine but its JSONL location on disk
    varies by build:
      - a directory/file named after the full key,
      - a file whose name embeds the trailing timestamp token (``<unix-ms>``),
      - a session directory containing ``*-attempt-*.jsonl`` whose name embeds
        the timestamp or the node id.

    We search both ``<oc_home>/agents/*/sessions/`` and ``<oc_home>/sessions/``
    and try: full key → trailing timestamp token → node-id-bearing session dirs.
    Returns the first `.jsonl` (or attempt file) found, else None.
    """
    if not child_key:
        return None

    # ── Strategy 0: sessions.json index (most precise, handles agent:<id>: prefix) ──
    idx_hit = _resolve_child_session_via_index(oc_home, child_key)
    if idx_hit:
        return idx_hit

    # ── Strategy 1-3: file-name scan (fallback) ──
    parts = [p for p in child_key.split(":") if p]
    ts_token = parts[-1] if parts else ""

    search_roots: list[str] = []
    agents_dir = os.path.join(oc_home, "agents")
    if os.path.isdir(agents_dir):
        for agent_name in os.listdir(agents_dir):
            sd = os.path.join(agents_dir, agent_name, "sessions")
            if os.path.isdir(sd):
                search_roots.append(sd)
    top_sessions = os.path.join(oc_home, "sessions")
    if os.path.isdir(top_sessions):
        search_roots.append(top_sessions)

    for sessions_dir in search_roots:
        try:
            entries = os.listdir(sessions_dir)
        except (OSError, PermissionError):
            continue

        # (1) Full-key .jsonl file or directory
        direct = os.path.join(sessions_dir, f"{child_key}.jsonl")
        if os.path.isfile(direct):
            return direct
        key_dir = os.path.join(sessions_dir, child_key)
        if os.path.isdir(key_dir):
            hit = _first_attempt_jsonl(key_dir)
            if hit:
                return hit

        # (2) Any entry whose name embeds the trailing timestamp token or node id
        for fname in entries:
            if ts_token and ts_token in fname:
                fpath = os.path.join(sessions_dir, fname)
                if fname.endswith(".jsonl") and os.path.isfile(fpath):
                    return fpath
                if os.path.isdir(fpath):
                    hit = _first_attempt_jsonl(fpath)
                    if hit:
                        return hit
            # session dirs named after the node id (e.g. subagent-check-...)
            if node_id and node_id in fname and os.path.isdir(os.path.join(sessions_dir, fname)):
                hit = _first_attempt_jsonl(os.path.join(sessions_dir, fname))
                if hit:
                    return hit

    return None


def _first_attempt_jsonl(session_dir: str) -> str | None:
    """Return the first ``*-attempt-*.jsonl`` (or any ``*.jsonl``) under a dir."""
    try:
        for fname in sorted(os.listdir(session_dir)):
            if "attempt" in fname and fname.endswith(".jsonl"):
                return os.path.join(session_dir, fname)
        # no attempt file? fall back to any .jsonl
        for fname in sorted(os.listdir(session_dir)):
            if fname.endswith(".jsonl"):
                return os.path.join(session_dir, fname)
    except (OSError, PermissionError):
        pass
    return None


def _find_session_jsonl_by_time(
    oc_home: str,
    node_id: str,
    ts_start: Any,
    ts_end: Any,
) -> str | None:
    """Fallback: find a session JSONL by scanning agent sessions created within
    the workflow's time window. Returns the best-matching path or None."""
    agents_dir = os.path.join(oc_home, "agents")
    if not os.path.isdir(agents_dir):
        return None

    candidates: list[tuple[float, str]] = []
    for agent_name in os.listdir(agents_dir):
        sessions_dir = os.path.join(agents_dir, agent_name, "sessions")
        if not os.path.isdir(sessions_dir):
            continue
        try:
            for fname in os.listdir(sessions_dir):
                fpath = os.path.join(sessions_dir, fname)
                # Direct .jsonl file
                if fname.endswith(".jsonl") and os.path.isfile(fpath):
                    mtime = os.path.getmtime(fpath)
                    if ts_start.timestamp() <= mtime <= ts_end.timestamp():
                        candidates.append((mtime, fpath))
                # Subdirectory with attempt files
                elif os.path.isdir(fpath):
                    for sub_fname in os.listdir(fpath):
                        if "attempt" in sub_fname and sub_fname.endswith(".jsonl"):
                            sub_path = os.path.join(fpath, sub_fname)
                            mtime = os.path.getmtime(sub_path)
                            if ts_start.timestamp() <= mtime <= ts_end.timestamp():
                                candidates.append((mtime, sub_path))
        except PermissionError:
            continue

    if not candidates:
        return None

    # Return the most recently modified one (closest match within the window)
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


# ---------------------------------------------------------------------------
# 5. YAML — DAG 结构
# ---------------------------------------------------------------------------

def _resolve_workflow_yaml(oc_home: str, workflow_id: str) -> str | None:
    """Resolve the workflow YAML path by searching packs directories."""
    candidates = [
        os.path.join(oc_home, "workflow", "packs"),
        os.path.join(oc_home, "workflows"),
        os.path.join(oc_home, "skills"),
    ]
    for base in candidates:
        for root, _dirs, files in os.walk(base):
            for f in files:
                if f.endswith(".yml") or f.endswith(".yaml"):
                    fpath = os.path.join(root, f)
                    try:
                        text = Path(fpath).read_text(encoding="utf-8")
                        # Quick check: does this YAML contain our workflow_id?
                        if workflow_id in text:
                            return fpath
                    except Exception:
                        continue
    return None


def _parse_yaml_dag(yaml_path: str) -> list[dict]:
    """Parse DAG structure from workflow YAML.

    Returns list of {node, executor, deps, title, skill_name, runtime_config}.
    """
    try:
        import yaml
    except ImportError:
        return _parse_yaml_dag_regex(yaml_path)

    try:
        text = Path(yaml_path).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
    except Exception:
        return []

    nodes = data if isinstance(data, list) else data.get("nodes", []) if isinstance(data, dict) else []
    dag = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id", "")
        if not nid:
            continue
        deps = node.get("dependsOn") or []
        if isinstance(deps, str):
            deps = [deps]
        runtime = {}
        if isinstance(node.get("runtimeConfig"), dict):
            rc = node["runtimeConfig"]
            runtime = {
                "model": rc.get("model"),
                "thinking": rc.get("thinking"),
            }
        dag.append({
            "node": nid,
            "executor": node.get("executor", node.get("type", "")),
            "deps": deps,
            "title": node.get("title") or node.get("description", ""),
            "skill_name": node.get("skillName") or node.get("skill", ""),
            "runtime_config": runtime,
        })
    return dag


def _parse_yaml_dag_regex(yaml_path: str) -> list[dict]:
    """Regex fallback for YAML DAG parsing."""
    try:
        text = Path(yaml_path).read_text(encoding="utf-8")
    except Exception:
        return []

    node_blocks = re.split(r'\n(?=-\s*id:)', '\n' + text)
    dag = []
    for block in node_blocks:
        id_m = re.search(r'-\s*id:\s*(\S+)', block)
        if not id_m:
            continue
        nid = id_m.group(1)
        exec_m = re.search(r'executor:\s*(\S+)', block)
        executor = exec_m.group(1) if exec_m else ""
        deps_m = re.search(r'dependsOn:\s*\[([^\]]*)\]', block)
        deps = []
        if deps_m:
            items = deps_m.group(1).strip()
            deps = [x.strip() for x in items.split(",") if x.strip()] if items else []
        title_m = re.search(r'title:\s*["\']?(.+?)["\']?\s*\n', block)
        skill_m = re.search(r'skillName:\s*(\S+)', block)
        dag.append({
            "node": nid,
            "executor": executor,
            "deps": deps,
            "title": title_m.group(1).strip() if title_m else "",
            "skill_name": skill_m.group(1) if skill_m else "",
            "runtime_config": {},
        })
    return dag


# ---------------------------------------------------------------------------
# 6. Main entry point
# ---------------------------------------------------------------------------

def build_workflow_trace(
    flow_id: str,
    workflow_id: str,
    oc_home: str,
    session_dir: str,
) -> dict:
    """Build a workflow trace dict from local sources.

    Args:
        flow_id: The workflow flow run ID.
        workflow_id: The workflow definition ID.
        oc_home: Path to the OpenClaw home directory.
        session_dir: Path to the embedded-sessions directory for this flow.

    Returns:
        A ``workflow_trace`` dict suitable for injection into ``__manifest__``.
        Empty dict on total failure (never raises).
    """
    trace: dict[str, Any] = {
        "flow_id": flow_id,
        "workflow_id": workflow_id,
        "status": "unknown",
        "goal": "",
        "started_at": None,
        "ended_at": None,
        "triggered_by": "",
        "dag": [],
        "node_executions": [],
        "timeline": [],
        "branches": [],
        "subagent_sessions": {},
        "usage": {},
        "audit_log": [],
        "workflow_outputs": {},
    }

    # ── 1. Registry metadata + state_json (PRIMARY source) ──
    # state_json is the engine's authoritative runtime snapshot: it carries
    # per-node status/result/usage, the full DAG snapshot, audit timeline, and
    # final outputs. It is the most complete source, so node_executions / dag
    # seeded here are only *supplemented* (never overwritten) by later steps.
    try:
        reg = _query_flow_registry(oc_home, flow_id)
        if reg:
            trace["status"] = reg.get("status", trace["status"])
            trace["goal"] = reg.get("goal", "")
            trace["started_at"] = reg.get("created_at")
            trace["ended_at"] = reg.get("ended_at")

            state_json = reg.get("state_json", "")
            if state_json:
                parsed = _parse_state_json(state_json)
                # Primary seed for node_executions / dag / usage / audit / outputs
                trace["node_executions"] = parsed["node_executions"]
                trace["dag"] = parsed["dag"]
                trace["usage"] = parsed["usage"]
                trace["audit_log"] = parsed["audit_log"]
                trace["workflow_outputs"] = parsed["workflow_outputs"]

                # Branch / trigger still pulled from the raw state dict
                try:
                    state = json.loads(state_json) if isinstance(state_json, str) else state_json
                    if isinstance(state, dict):
                        trace["triggered_by"] = state.get("triggered_by", state.get("trigger", ""))
                        branches = state.get("branches", state.get("branchDecisions", []))
                        if isinstance(branches, list):
                            trace["branches"] = branches
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
    except Exception:
        pass

    # ── 2. Engine log — SUPPLEMENT node_executions + supply timeline + wf meta ──
    # state_json (step 1) is primary; engine-log node stats fill any fields
    # state_json left empty (duration/executor/session_id for nodes present only
    # in the event stream) and supply the authoritative timeline. state_json's
    # output/status are never overwritten (see _merge_executions).
    try:
        node_execs, timeline, wf_meta = _build_node_executions_from_logs(flow_id, oc_home)
        if trace.get("node_executions") or node_execs:
            trace["node_executions"] = _merge_executions(
                trace.get("node_executions") or [], node_execs or []
            )
        if timeline:
            trace["timeline"] = timeline
        # Engine-log timestamps are more precise than registry created_at/ended_at.
        if wf_meta.get("started_at"):
            trace["started_at"] = wf_meta["started_at"]
        if wf_meta.get("ended_at"):
            trace["ended_at"] = wf_meta["ended_at"]
        if wf_meta.get("status") and trace.get("status") in ("unknown", None, ""):
            trace["status"] = wf_meta["status"]
    except Exception:
        pass

    # ── 3. engine.db — probe for additional node output/result data ──
    try:
        db_outputs = _probe_engine_db(oc_home, flow_id)
        if db_outputs:
            for entry in trace["node_executions"]:
                nid = entry.get("node_id", "")
                if nid in db_outputs:
                    extras = db_outputs[nid]
                    if entry.get("output") is None:
                        # Try to find a meaningful output field
                        for key in ("output", "result", "data", "response"):
                            if key in extras:
                                val = extras[key]
                                if isinstance(val, str):
                                    try:
                                        entry["output"] = json.loads(val)
                                    except (json.JSONDecodeError, TypeError):
                                        entry["output"] = val
                                else:
                                    entry["output"] = val
                                break
    except Exception:
        pass

    # ── 4. Resolve subagent external session paths FIRST ──
    # Required before collecting agent outputs: subagent sessions live outside
    # session_dir and _collect_agent_outputs reads their JSONL via this map.
    try:
        trace["subagent_sessions"] = _resolve_subagent_session_paths(
            oc_home, trace["node_executions"]
        )
    except Exception:
        pass

    # ── 4b. Per-node session JSONL — agent node outputs ──
    # Fills output only where state_json.result didn't (state_json wins).
    try:
        _collect_agent_outputs(
            session_dir, trace["node_executions"], trace.get("subagent_sessions") or {}
        )
    except Exception:
        pass

    # ── 5. YAML — DAG FALLBACK (only when state_json.workflowSnapshot was empty) ──
    # workflowSnapshot (step 1) is the runtime DAG snapshot and is preferred; we
    # only fall back to scanning for a YAML file on disk when that yielded nothing.
    if not trace.get("dag"):
        try:
            yaml_path = _resolve_workflow_yaml(oc_home, workflow_id)
            if yaml_path:
                trace["dag"] = _parse_yaml_dag(yaml_path)
        except Exception:
            pass

    return trace


# ---------------------------------------------------------------------------
# CLI (for standalone testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Build workflow trace from local sources")
    parser.add_argument("flow_id", help="Workflow flow run ID")
    parser.add_argument("workflow_id", help="Workflow definition ID")
    parser.add_argument("--openclaw-home", default=os.path.expanduser("~/.openclaw"),
                        help="OpenClaw home directory")
    parser.add_argument("--session-dir", help="Path to embedded-sessions directory for this flow")
    args = parser.parse_args()

    if not args.session_dir:
        from session_io import resolve_session_root, find_session_dir
        session_root = resolve_session_root(args.openclaw_home)
        session_dir = find_session_dir(args.flow_id, args.workflow_id, session_root=session_root)
    else:
        session_dir = args.session_dir

    if not session_dir:
        print(f"ERROR: session directory not found for flow_id={args.flow_id}", file=sys.stderr)
        sys.exit(1)

    trace = build_workflow_trace(args.flow_id, args.workflow_id, args.openclaw_home, session_dir)
    print(json.dumps(trace, indent=2, ensure_ascii=False, default=str))