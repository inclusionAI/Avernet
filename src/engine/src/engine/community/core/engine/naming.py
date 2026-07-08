"""
Engine-name normalization.

Engine names are user-facing strings that travel through multiple channels —
HTTP params, DB columns (``ac_bots.engine_types`` / ``active_engine``), config
files (``CHAT_ENGINE`` env var, ``engine.json``), and DaaS CLI flags
(``start_service.sh --engine ...``).

Each of those sources has evolved its own spelling:

* Internal Python / config: ``aicoding`` (domain name, canonical)
* Legacy snake_case (DB / old Python identifiers): ``claude_code``
* Legacy hyphen (migration doc / some config): ``claude-code``
* Legacy PascalCase: ``ClaudeCode``

``normalize()`` collapses every known spelling to a single canonical form so
that downstream dispatch (``EngineRegistry``, ``EngineManager.switch``) only
has to match against one value per engine. Unknown names are passed through
unchanged, so a new engine can be added by just registering it under its
canonical name — no need to update a central alias map unless a legacy
spelling needs to be honored.

The canonical set today:

* ``openclaw``
* ``moltis``
* ``aicoding``
* ``claude_code`` (aliases: ``claude-code``, ``claudecode``)

Design choice: the canonical is the **domain name** form (``aicoding``) —
which is what the DaaS bootstrapping CLI uses and what the internal branding
now aligns on. The legacy ``claude_code`` / ``claude-code`` / ``ClaudeCode``
spellings are kept as aliases so that existing DB rows, user preferences and
frontend payloads continue to resolve without a data migration.
"""
from __future__ import annotations

import logging

log = logging.getLogger("engine-naming")

# Canonical → set of aliases (lower-cased). ``normalize()`` lowercases input
# then looks them up here; absent names fall through to the lower-cased input.
_ALIASES: dict[str, frozenset[str]] = {
    "openclaw": frozenset({"openclaw"}),
    "moltis": frozenset({"moltis"}),
    "aicoding": frozenset({"aicoding"}),
    "claude_code": frozenset(
        {
            "claude_code",
            "claude-code",   # legacy hyphen (migration doc)
            "claudecode",    # legacy PascalCase lower-cased
        }
    ),
}


def normalize(name: str | None) -> str:
    """Return the canonical engine name for ``name``.

    Args:
        name: the raw engine name from any external source. ``None`` or empty
            strings are treated as unknown and returned as the empty string so
            callers can raise their own "missing engine" error if appropriate.

    Returns:
        The canonical name. Unknown names are passed through in their
        lower-cased form — this is intentional so that registering a brand-new
        engine (e.g. ``"lingxi"``) works without editing this module.
    """
    if not name:
        return ""
    lowered = name.strip().lower()
    for canonical, aliases in _ALIASES.items():
        if lowered in aliases:
            if lowered != canonical:
                log.debug("[normalize] engine alias resolved: %s → %s", lowered, canonical)
            return canonical
    log.debug("[normalize] unknown engine name (pass-through): %s", lowered)
    return lowered


def known_canonicals() -> tuple[str, ...]:
    """Return the set of canonical engine names this module knows about.

    Intended for diagnostics / startup logging only — do **not** use this as a
    whitelist for registration. An engine that registers itself under a name
    not in this tuple is still valid; it just means no aliases are declared.
    """
    return tuple(_ALIASES.keys())


__all__ = ["normalize", "known_canonicals"]
