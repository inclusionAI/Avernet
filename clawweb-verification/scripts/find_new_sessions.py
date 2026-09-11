#!/usr/bin/env python3
"""Safely enumerate OpenClaw NAS session files newer than a verification boundary."""

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

DEFAULT_BASES = (
    "/home/admin/.bot_shared_nas/arca/arcaagentclaw/prod",
    "/home/admin/.bot_shared_nas_2/arca/arcaagentclaw/prod",
)


def parse_time(value):
    normalized = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def session_id(path):
    name = path.name
    return name[:-6] if name.endswith(".jsonl") else name


def main():
    parser = argparse.ArgumentParser(description="List post-repair OpenClaw NAS sessions")
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--bot-id", required=True)
    parser.add_argument("--since", required=True, help="ISO-8601 handledAt boundary")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--base", action="append", dest="bases")
    args = parser.parse_args()

    if args.limit < 1 or args.limit > 1000:
        raise ValueError("limit 必须是 1 到 1000")
    since_epoch = parse_time(args.since)
    bases = tuple(args.bases or DEFAULT_BASES)
    patterns = (
        "prod_staff_%s_openclaw_%s*" % (args.user_id, args.bot_id),
        "prod_staff_%s_claude_code_%s*" % (args.user_id, args.bot_id),
    )
    roots = []
    for base_text in bases:
        base = Path(base_text)
        if not base.is_dir():
            continue
        for pattern in patterns:
            roots.extend(path for path in base.glob(pattern) if path.is_dir())

    items = []
    for root in sorted(set(roots)):
        agents = root / ".openclaw" / "agents"
        if not agents.is_dir():
            continue
        for path in agents.glob("*/sessions/*.jsonl"):
            if path.name.endswith(".trajectory.jsonl"):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime <= since_epoch:
                continue
            agent_name = path.parent.parent.name
            items.append({
                "sessionId": session_id(path),
                "agent": agent_name,
                "path": str(path),
                "root": str(root),
                "modifiedAt": dt.datetime.fromtimestamp(
                    stat.st_mtime, tz=dt.timezone.utc
                ).astimezone().isoformat(timespec="seconds"),
                "sizeBytes": stat.st_size,
            })

    items.sort(key=lambda item: item["modifiedAt"], reverse=True)
    result = {
        "userId": args.user_id,
        "botId": args.bot_id,
        "since": args.since,
        "roots": [str(path) for path in sorted(set(roots))],
        "count": min(len(items), args.limit),
        "hasMore": len(items) > args.limit,
        "items": items[: args.limit],
        "note": "mtime 仅用于召回；验收前必须读取 Session 内真实时间并确认与原根因相关。",
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print("Error: %s" % error, file=sys.stderr)
        sys.exit(1)
