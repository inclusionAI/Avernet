from __future__ import annotations

import os
import re
from pathlib import Path

from ..models import Diagnosis


def _terms(diag: Diagnosis) -> list[str]:
    text = " ".join([diag.query, diag.common_problem_key, diag.session.tool_text])
    terms = set(re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{3,}|[\u4e00-\u9fff]{2,}", text))
    return [t for t in terms if len(t) >= 2][:12]


def enrich_environment_evidence(
    diags: list[Diagnosis], layout: dict, max_files: int = 120
) -> None:
    roots = [
        Path(p).expanduser()
        for p in (layout.get("skill_dirs", []) + layout.get("doc_dirs", []))
    ]
    candidates: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in _iter_small_candidate_files(root, max_files - len(candidates)):
            candidates.append(path)
            if len(candidates) >= max_files:
                break
        if len(candidates) >= max_files:
            break
    for diag in diags:
        terms = _terms(diag)
        existing_evidence = [e for e in (diag.evidence or []) if isinstance(e, dict)]
        evidence = list(existing_evidence)
        for path in candidates:
            try:
                txt = path.read_text(errors="ignore")[:8000]
            except Exception:
                continue
            hit = next((t for t in terms if t and t.lower() in txt.lower()), "")
            if hit:
                snippet = re.sub(
                    r"\s+",
                    " ",
                    txt[
                        max(0, txt.lower().find(hit.lower()) - 80) : txt.lower().find(
                            hit.lower()
                        )
                        + 220
                    ],
                ).strip()
                evidence.append(
                    {
                        "source": "environment",
                        "path": str(path),
                        "term": hit,
                        "snippet": snippet[:300],
                    }
                )
            if len(evidence) >= max(3, len(existing_evidence) + 3):
                break
        if not evidence:
            evidence.append(
                {
                    "source": "session_only",
                    "path": diag.session.path,
                    "snippet": diag.root_cause_summary,
                }
            )
        diag.evidence = evidence
        existing_hints = [h for h in (diag.evidence_file_hints or []) if isinstance(h, dict)]
        env_hints = [e for e in evidence if e.get("path")]
        seen_paths: set[str] = set()
        merged_hints = []
        for hint in existing_hints + env_hints:
            path_key = str(hint.get("path") or "")
            if path_key and path_key in seen_paths:
                continue
            if path_key:
                seen_paths.add(path_key)
            merged_hints.append(hint)
        diag.evidence_file_hints = merged_hints


def _iter_small_candidate_files(root: Path, remaining: int):
    if remaining <= 0:
        return
    suffixes = {".md", ".json", ".yaml", ".yml", ".log"}
    ignored = {"node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", "target", ".cache"}
    max_dirs = 2000
    seen_dirs = 0
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            seen_dirs += 1
            if seen_dirs > max_dirs:
                break
            dirnames[:] = [d for d in dirnames if d not in ignored and not d.startswith(".")]
            for filename in filenames:
                path = Path(dirpath) / filename
                if filename == "SKILL.md" or path.suffix in suffixes:
                    yield path
    except OSError:
        return
