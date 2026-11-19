"""Conservative snapshot redaction, not a complete production DLP policy.

Recognizable credentials mirror the pre-push scanner's credential families;
assignments are deliberately stricter (no entropy/placeholder exemptions).
Only normalized text enters the store, never engine metadata/tool payloads.
"""

from __future__ import annotations

import re

_PATTERNS = (
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)",
    r"\bLTAI[0-9A-Za-z]{12,30}\b",
    r"\bSTS\.[0-9A-Za-z]{8,64}\b",
    r"\bCAIS[A-Za-z0-9_+=/-]{16,}\b",
    r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
    r"\bglpat-[A-Za-z0-9_-]{20,}\b",
    r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b",
    r"\bAIza[0-9A-Za-z_-]{30,}\b",
    r"\bsk-(?:[A-Za-z0-9]+-)*[A-Za-z0-9_-]{20,}\b",
    r"\bnpm_[A-Za-z0-9]{20,}\b",
    r"\bpypi-[A-Za-z0-9_-]{20,}\b",
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b",
    r"(?i)\bBearer[ \t]+[A-Za-z0-9._~+/=-]+",
    r"""(?ix)(?:^|[^a-z0-9])(?:[a-z0-9]+[._-])*
        (?:api[._-]?key|access[._-]?key(?:[._-]?(?:id|secret))?|
        secret(?:[._-]?key)?|private[._-]?key|client[._-]?secret|
        auth[._-]?token|password|passwd|credential|token)["']?\s*(?:[:=]|=>)\s*
        (?:["'][^"'\r\n]*["']|[^\s,;"']+)""",
    r"""(?ix)(?:^|[^a-z0-9])(?:alibaba(?:[._-]?cloud)?|aliyun|oss)[a-z0-9._-]*
        (?:access[._-]?key(?:[._-]?(?:id|secret))?|ak|sk)\b["']?\s*(?:[:=]|=>)\s*
        (?:["'][^"'\r\n]*["']|[^\s,;"']+)""",
    r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)",
)
_RULES = tuple(re.compile(pattern) for pattern in _PATTERNS)


def redact_text(text: str) -> str:
    for pattern in _RULES:
        text = pattern.sub("[REDACTED]", text)
    return text
