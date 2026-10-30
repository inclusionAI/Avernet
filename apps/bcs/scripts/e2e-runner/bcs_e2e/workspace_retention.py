"""Bounded response retention for the dual-environment Workspace density seam.

The legacy shell harness asserted inline on raw response bodies and CLI
stdout.  The owned-local Workspace executors journaled intent, status and
request identity but deliberately dropped the response value, so a density
projector had nothing retained to project field-level atoms from.  This module
adds the missing observation surface with hard bounds:

- only owned-local fixture projections (Group rows, bot UUIDs, labels, routing
  policy, service_spec, message IDs, CLI stdout) are retained;
- tokens, cookies and session credentials never appear in these bodies and
  never enter a journal;
- values are normalized to JSON-safe structures and truncated past explicit
  byte budgets, so journal size stays bounded regardless of product payload.
"""
from __future__ import annotations

import hashlib
import json

RETAIN_VALUE_BYTES = 16_384
RETAIN_STDOUT_BYTES = 8_192
MAX_RETAINED_READS = 64


def retain_value(value: object) -> dict:
    """Return a JSON-safe bounded copy of an observed response value."""
    try:
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        return {'_density_truncated': True, 'sha256': _digest(str(value))}
    if not isinstance(value, dict) or len(payload.encode('utf-8')) > RETAIN_VALUE_BYTES:
        return {'_density_truncated': True, 'sha256': _digest(payload)}
    return json.loads(payload)


def retain_text(text: str) -> tuple[str, bool]:
    """Return bounded CLI stdout and whether the original was truncated."""
    encoded = text.encode('utf-8', errors='replace')
    if len(encoded) <= RETAIN_STDOUT_BYTES:
        return text, False
    return encoded[:RETAIN_STDOUT_BYTES].decode('utf-8', errors='replace'), True


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
