"""Exact OpenClaw sessions.json -> JSONL collection, not newest-file fallback.

Follows clawbench-base lib_agent's exact-session lookup approach. No workflow
embedded-session discovery or arbitrary time-window scan is used here.
"""
import json
import math
import shutil
from pathlib import Path

def collect_member(profile_root, session, output):
    root, output = Path(profile_root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    files, missing, events, seen = [], [], [], set()
    for index in root.glob("agents/*/sessions/sessions.json"):
        store = json.loads(index.read_text())
        for key, record in store.items():
            # OpenClaw route key ends with canonical BCS Session peer ID.
            if not (key == session or key.endswith(":" + session)):
                continue
            if not isinstance(record, dict):
                continue
            sid = record.get("sessionId")
            if not isinstance(sid, str) or Path(sid).name != sid:
                missing.append("invalid OpenClaw session id")
                continue
            candidates = [index.parent / f"{sid}.jsonl", index.parent / f"{sid}.ndjson"]
            if record.get("sessionFile"):
                candidates.insert(0, Path(record["sessionFile"]))
            source = next((p for p in candidates if p.is_file() and p.resolve().is_relative_to(root.resolve())), None)
            if source is None:
                missing.append(f"transcript missing for {sid}")
                continue
            if str(source.resolve()) in seen:
                continue
            seen.add(str(source.resolve()))
            dest = output / f"{index.parent.parent.name}-{source.name}"
            shutil.copyfile(source, dest)
            files.append(str(dest))
            for line in source.read_text().splitlines():
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    missing.append("incomplete JSONL line")
    if not files:
        missing.append("no exact BCS Session mapping found")
    total, count, tool_calls, usage_complete = 0, 0, 0, bool(files)
    message_ids = set()
    for event in events:
        msg = event.get("message", {})
        if msg.get("role") != "assistant":
            continue
        event_id = event.get("id")
        if event_id and event_id in message_ids:
            continue
        if event_id:
            message_ids.add(event_id)
        count += 1
        content = msg.get("content", [])
        if isinstance(content, list):
            tool_calls += sum(1 for block in content if isinstance(block, dict) and block.get("type") in ("toolCall", "tool_use"))
        tokens = msg.get("usage", {}).get("totalTokens")
        if type(tokens) not in (int, float) or not math.isfinite(tokens) or tokens < 0:
            usage_complete = False
        else:
            total += tokens
    if not count:
        usage_complete = False
    return {"files": files, "missing": missing, "assistant_messages": count, "tool_calls": tool_calls if files and not missing else None,
            "total_tokens": total if usage_complete and not missing else None,
            "evidence_complete": bool(files) and not missing}
