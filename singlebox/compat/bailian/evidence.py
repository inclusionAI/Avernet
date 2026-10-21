"""Allowlisted probe evidence; never export engine transcripts or credentials."""
import json
from pathlib import Path
import re


def marker_evidence(text: str, marker: str) -> dict:
    return {"contains_expected": marker in text,
            "probe_tokens": re.findall(r"BCS_[a-f0-9]{1,64}", text),
            "characters": len(text)}


def openclaw_evidence(root: Path, marker: str, final: str) -> dict:
    rows = []
    for path in sorted(root.rglob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            message = entry.get("message", {})
            role = message.get("role")
            if role not in ("assistant", "toolResult", "tool"):
                continue
            content = message.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    rows.append({"role": role, **marker_evidence(block.get("text", ""), marker)})
                elif block.get("type") in ("toolCall", "tool_use"):
                    # Tool arguments and hidden reasoning are deliberately excluded.
                    rows.append({"role": role, "tool_call": True})
    return {"expected_probe": marker, "native_messages": rows,
            "transcript_files": [str(p.relative_to(root)) for p in root.rglob("*.jsonl")],
            "cli_final": marker_evidence(final, marker)}
