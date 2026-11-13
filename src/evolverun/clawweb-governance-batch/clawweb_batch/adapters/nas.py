"""Exact source-scoped NAS reads; no writes, glob-wide log scans, or credential dumps."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..evidence import redact


class NASReader:
    def __init__(self, roots: tuple[Path, ...]):
        self.roots = roots

    def available(self) -> list[dict]:
        return [{"root": str(p), "available": p.is_dir()} for p in self.roots]

    def scope_root(self, source: str, owner: str, bot: str) -> Path | None:
        if "aidesktop" in source or source == "unknown":
            return None
        # Match an observed full runtime directory, never choose a different DEVICE variant.
        names = [part for part in Path(source).parts
                 if part in {f"prod_staff_{owner}_openclaw_{bot}", f"prod_staff_{owner}_claude_code_{bot}"}
                 or part.startswith((f"prod_staff_{owner}_openclaw_{bot}_DEVICE-",
                                     f"prod_staff_{owner}_claude_code_{bot}_DEVICE-"))]
        if len(names) != 1:
            return None
        for base in self.roots:
            root = base / names[0]
            if root.is_dir() and root.resolve().is_relative_to(base.resolve()):
                return root
        return None

    def sources(self, owner: str, bot: str) -> dict:
        """Enumerate exact Owner+Bot runtime/agent directories, including idle bots.

        Missing roots, symlinks and unreadable directories make coverage incomplete.
        A missing/disabled Cron job is not itself an error; its runtime is still read.
        """
        import re
        if any(not re.fullmatch(r"[\w.-]+", value) for value in (owner, bot)):
            raise ValueError("invalid NAS identity")
        sources, warnings = [], []
        prefixes = (f"prod_staff_{owner}_openclaw_{bot}", f"prod_staff_{owner}_claude_code_{bot}")
        for base in self.roots:
            try:
                for index, runtime in enumerate(base.iterdir()):
                    if index >= 100000:
                        warnings.append("runtime directory enumeration exceeds bound")
                        break
                    if not any(runtime.name == p or runtime.name.startswith(p + "_DEVICE-") for p in prefixes):
                        continue
                    if runtime.is_symlink() or not runtime.is_dir():
                        warnings.append("nonregular runtime directory")
                        continue
                    agents = runtime / ".openclaw" / "agents"
                    if not agents.resolve().is_relative_to(runtime.resolve()):
                        warnings.append("agent directory escapes runtime")
                        continue
                    for agent in agents.iterdir():
                        if agent.is_symlink():
                            warnings.append("symlinked agent directory")
                        elif agent.is_dir() and (agent / "sessions").is_dir():
                            sources.append(str(agent))
                        else:
                            warnings.append("agent session directory unavailable")
            except OSError:
                warnings.append("runtime or agent directory unavailable")
        if not sources:
            warnings.append("no current Owner+Bot session sources")
        return {"sources": sorted(set(sources)), "complete": not warnings, "warnings": warnings}

    def inspect(self, tasks: list[dict], owner: str, bot: str) -> list[dict]:
        result, seen = [], set()
        for task in tasks:
            source = task["source"]
            if source in seen:
                continue
            seen.add(source)
            root = self.scope_root(source, owner, bot)
            item = {"source": source, "current_scope_verified": root is not None, "files": []}
            if root is not None:
                paths = [".openclaw/workspace/AGENTS.md", ".openclaw/workspace/TOOLS.md"]
                for t in tasks:
                    if t["source"] != source:
                        continue
                    for p in t["config_paths"]:
                        for prefix in ("~/", "/home/admin/"):
                            if p.startswith(prefix):
                                p = p[len(prefix):]
                                break
                        if p.startswith(".openclaw/") and ".." not in Path(p).parts:
                            paths.append(p)
                for rel in dict.fromkeys(paths):
                    if len(item["files"]) >= 6:
                        break
                    path = root / rel
                    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
                        continue
                    size = path.stat().st_size
                    if size > 64000:
                        item["files"].append({"path": rel, "status": "too_large", "bytes": size})
                        continue
                    data = path.read_bytes()
                    item["files"].append({"path": rel, "sha256": hashlib.sha256(data).hexdigest(), "bytes": size,
                                          "text_excerpt": redact(data.decode(errors="replace"))[:3000],
                                          "excerpt_only": len(data) > 3000})
            result.append(item)
        return result

    def scan_since(self, source: str, owner: str, bot: str, since: str, max_bytes: int) -> dict:
        """Cover the offline-to-current gap for one exact NAS runtime/agent, never the full NAS."""
        import json
        import os
        from ..core import instant
        from ..evidence import extract_tasks
        root = self.scope_root(source, owner, bot)
        if root is None:
            return {"tasks": [], "complete": False, "warnings": ["current source unavailable"]}
        tail = source.split(root.name, 1)[1].lstrip("/")
        if tail.startswith("openclaw/"):
            tail = "." + tail
        directory = root / tail / "sessions"
        if not directory.resolve().is_relative_to(root.resolve()) or not directory.is_dir():
            return {"tasks": [], "complete": False, "warnings": ["session directory unavailable"]}
        cutoff, rows, warnings, examined = instant(since), [], [], 0
        with os.scandir(directory) as entries:
            for entry in entries:
                examined += 1
                if examined > 5000:
                    warnings.append("directory exceeds bounded enumeration")
                    break
                if not entry.name.endswith(".jsonl") or ".trajectory." in entry.name:
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    warnings.append("nonregular session entry")
                    continue
                info = entry.stat(follow_symlinks=False)
                if info.st_mtime < cutoff.timestamp():
                    continue
                if info.st_size > max_bytes:
                    warnings.append("oversized recent session")
                    continue
                messages = []
                try:
                    with open(entry.path, encoding="utf-8") as handle:
                        for line in handle:
                            item = json.loads(line)
                            if item.get("type") != "message" or not isinstance(item.get("message"), dict):
                                continue
                            message = dict(item["message"])
                            message.setdefault("timestamp", item.get("timestamp"))
                            messages.append(message)
                    stamps = [instant(str(m["timestamp"])) for m in messages if m.get("timestamp")]
                    if not stamps or len(stamps) != len(messages):
                        warnings.append("recent session missing timestamps")
                        continue
                    if max(stamps) <= cutoff:
                        continue
                    sid = entry.name[:-6]
                    rows.append({"user_id": owner, "bot_id": bot, "session_id": sid,
                                 "dt": max(stamps).strftime("%Y%m%d"), "start_time": min(stamps).isoformat(),
                                 "end_time": max(stamps).isoformat(), "sampling_group_key": "",
                                 "file_path": source + "/sessions/" + entry.name,
                                 "messages": json.dumps(messages), "llm_tasks_json": json.dumps([
                                     {"task_index": 0, "message_range": [0, len(messages)], "is_complete": 2,
                                      "task_failure_class": "UNKNOWN", "task_description": "current runtime follow-up"}])})
                except (ValueError, OSError, TypeError):
                    warnings.append("unreadable or incomplete recent session")
        tasks, parse_warnings = extract_tasks(rows, max_bytes)
        return {"tasks": tasks, "complete": not warnings and not parse_warnings,
                "warnings": warnings + parse_warnings, "enumerated_entries": examined}
