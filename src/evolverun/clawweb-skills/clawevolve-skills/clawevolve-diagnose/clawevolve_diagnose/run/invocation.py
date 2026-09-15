from __future__ import annotations

import re

from ..utils import redact_secrets

_INVOCATION_PREFIXES = ("/clawevolve-diagnose", "clawevolve-diagnose", "$clawevolve-diagnose")


def unsupported_flag_args(unknown: list[str]) -> list[str]:
    return [arg for arg in unknown if str(arg).startswith("--")]


def normalize_invocation_message(message: str) -> str:
    """Strip a preserved slash-command prefix from natural-language message text."""
    text = (message or "").strip()
    for prefix in _INVOCATION_PREFIXES:
        if text == prefix:
            return ""
        if text.startswith(prefix + " "):
            return text[len(prefix) :].strip()
    return text


def scrub_message_secrets(message: str, api_key: str) -> str:
    text = re.sub(r"(?i)(?:^|\s)--api-key(?:=|\s+)('(?:[^']*)'|\"(?:[^\"]*)\"|\S+)", " ", str(message or "")).strip()
    return redact_secrets(text, [secret for secret in [api_key] if secret]).strip()
