"""Public projection of ``ac_templates.ext`` (``template_config``).

``template_config`` stored engine-side legitimately carries secrets: the
aicoding provisioning strategy persists ``bot_template_config.ext_config
.thetaKey`` as an ``enc:v1:`` ciphertext, and the stable outer contract allows
a plain ``token``. Listing responses must never echo either — an encrypted
blob is still offline attack material and a replayable oracle.

Rules for this file:
- Allowlist, never denylist: engine-owned extensions surface only when a key
  is added here explicitly (plus security review). Default is "dropped".
- The result is a fresh shallow+container copy: callers may mutate it without
  aliasing the stored snapshot.
"""

from __future__ import annotations

from typing import Any, Mapping

#: Keys that are display-safe. Keep alphabetized.
_PUBLIC_TEMPLATE_KEYS = (
    "code_repos",
    "devflow_workflow",
    "template_key",
    "template_uid",
    "yuque_kb_repos",
)


def project_template_config_for_public(
    config: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Project a stored template snapshot onto its public display subset."""
    if not isinstance(config, Mapping) or not config:
        return None
    if not any(key in config for key in _PUBLIC_TEMPLATE_KEYS):
        return None
    projected: dict[str, Any] = {}
    for key in _PUBLIC_TEMPLATE_KEYS:
        if key in config:
            value = config[key]
            if isinstance(value, list):
                projected[key] = list(value)
            elif isinstance(value, dict):
                projected[key] = dict(value)
            else:
                projected[key] = value
    return projected
