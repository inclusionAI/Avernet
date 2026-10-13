"""Content-safety and egress controls (task 12.9).

- PII classification of stored swarm content (goal, messages, shards):
  pattern families for the classes an operator must report on, with a
  redactor for export surfaces.
- Egress allowlist: the single outbound surface (the LLM endpoint) is
  validated at client construction against the
  configured allowlist — fail clear at boot, not per request.
- Prompt-injection heuristics: subagent output is untrusted data that
  flows into orchestrator prompts; markers detectable here are recorded
  as guard events, and the orchestrator wraps results in a data fence.

All checks are heuristics, not guarantees; the OPERATIONS.md data
protection section documents the classification and the controls map.
"""
from __future__ import annotations

import re
from enum import StrEnum
from urllib.parse import urlparse

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[ -]?)?\d{3}[ -]\d{4}(?!\d)")
_CARD = re.compile(r"(?<!\d)\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}(?!\d)")
_NATIONAL_ID = re.compile(r"(?<!\d)\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)")

_INJECTION_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions", re.compile(
        r"ignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?)", re.I)),
    ("role_override", re.compile(
        r"(?:you\s+are\s+now|act\s+as(?:\s+if)?\s+(?:you\s+are|a\s+new))\s+"
        r"(?:(?:the|a|a\s+new|new)\s+)?(?:system|developer|root|admin)", re.I)),
    ("exfiltrate_secret", re.compile(
        r"(?:reveal|print|exfiltrate|send|repeat)\s+.{0,32}"
        r"(?:system\s*prompt|instructions|api[_ ]?key|secret|credential)", re.I)),
)

class PiiKind(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    CARD = "card"
    NATIONAL_ID = "national_id"


_REDACTIONS: dict[PiiKind, re.Pattern[str]] = {
    PiiKind.EMAIL: _EMAIL,
    PiiKind.PHONE: _PHONE,
    PiiKind.CARD: _CARD,
    PiiKind.NATIONAL_ID: _NATIONAL_ID,
}


def classify_pii(text: str) -> tuple[PiiKind, ...]:
    """Every PII family present in ``text``, deduplicated, in enum order."""
    if not text:
        return ()
    found = {kind for kind, pattern in _REDACTIONS.items() if pattern.search(text)}
    ordered = []
    for kind in _REDACTIONS:
        if kind in found:
            ordered.append(kind)
    return tuple(ordered)


def redact_pii(text: str) -> str:
    """Replace every classified pattern with its family tag."""
    for kind, pattern in _REDACTIONS.items():
        text = pattern.sub(f"[REDACTED:{kind.value}]", text)
    return text


def detect_prompt_injection(text: str) -> tuple[str, ...]:
    """Injection marker ids found in untrusted subagent output."""
    if not text:
        return ()
    return tuple(
        marker for marker, pattern in _INJECTION_MARKERS
        if pattern.search(text)
    )


def validate_egress_url(url: str, *, allowed_hosts: tuple[str, ...],
                        require_https: bool, surface: str) -> str:
    """Fail-clear gate for outbound endpoints (task 12.9 egress allowlist).

    ``AgentComputeConfigError`` names the violating surface so a bad
    deployment fails at composition time instead of leaking on the first
    request. Rules: only http(s) schemes; optional https-only; when an
    allowlist is configured the host must be in it.
    """
    from agentclaw.community.core.agentcompute.service.protocols import (
        AgentComputeConfigError,
    )

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise AgentComputeConfigError(
            f"agentcompute {surface} egress url scheme not allowed: {parsed.scheme!r}"
        )
    if require_https and parsed.scheme != "https":
        raise AgentComputeConfigError(
            f"agentcompute {surface} egress must be https: {url!r}"
        )
    if allowed_hosts and parsed.hostname not in allowed_hosts:
        raise AgentComputeConfigError(
            f"agentcompute {surface} egress host {parsed.hostname!r} "
            f"not in allowlist {sorted(allowed_hosts)}"
        )
    return url