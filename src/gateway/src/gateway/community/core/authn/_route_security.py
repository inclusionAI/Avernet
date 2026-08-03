"""Route → identity-requirement table (auth design §8, reshaped).

The value under each ``"[METHOD ]<path-glob>"`` key is a mapping of
``{identity: required|optional}`` (identity = a ``PrincipalType`` value).
Resolves an incoming ``(method, path)`` to the **most specific** matching rule's
requirement. Fail-closed: an unmatched route resolves to ``None`` and the caller
must deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from gateway.community.core.paths import PathPattern, split_segments
from gateway.community.spi.authn import Presence, PrincipalType

# A requirement maps each identity the route cares about to its Presence.
Requirement = dict[PrincipalType, Presence]


@dataclass(frozen=True)
class _Rule:
    method: str | None  # None = applies to every method
    pattern: PathPattern
    requirement: Requirement


class RouteSecurity:
    """The compiled route-security table, queryable per request."""

    def __init__(self, rules: list[_Rule]) -> None:
        self._rules = rules

    @classmethod
    def from_table(cls, table: dict[str, Any]) -> RouteSecurity:
        return cls([_parse_rule(key, value) for key, value in table.items()])

    @classmethod
    def from_yaml(cls, path: str | Path) -> RouteSecurity:
        raw = yaml.safe_load(Path(path).read_text()) or {}
        user_config = raw.get("user_config", {}) if isinstance(raw, dict) else {}
        table = (
            user_config.get("route_security", {})
            if isinstance(user_config, dict)
            else {}
        )
        return cls.from_table(table)

    def resolve(self, method: str, path: str) -> Requirement | None:
        """Most-specific matching rule's requirement, or ``None`` if none match."""
        segments = split_segments(path)
        matches = [r for r in self._rules if _matches(r, method, segments)]
        if not matches:
            return None
        return max(matches, key=_specificity).requirement


# ── parsing ──────────────────────────────────────────────────────────────────


def _parse_rule(key: str, value: Any) -> _Rule:
    method, path = _split_key(key)
    return _Rule(
        method=method,
        pattern=PathPattern.parse(path),
        requirement=_parse_req(value),
    )


def _split_key(key: str) -> tuple[str | None, str]:
    parts = key.strip().split(None, 1)
    if len(parts) == 2 and parts[0].isupper() and parts[1].startswith("/"):
        return parts[0], parts[1]
    return None, key.strip()


def _parse_req(value: Any) -> Requirement:
    """Each value is ``{<identity-type-value>: required|optional}``."""
    req: Requirement = {}
    items = cast(dict[str, str], value or {})
    for identity_value, presence_value in items.items():
        identity = PrincipalType(identity_value)
        presence = Presence(presence_value)
        req[identity] = presence
    return req


# ── matching (§8.3) ──────────────────────────────────────────────────────────


def _matches(rule: _Rule, method: str, path_segments: tuple[str, ...]) -> bool:
    if rule.method is not None and rule.method != method:
        return False
    return rule.pattern.matches(path_segments)


def _specificity(rule: _Rule) -> tuple[int, int, int, int]:
    """Higher = more specific: the shared path ranking, then method.

    The path terms come from :attr:`PathPattern.specificity`, shared with the
    domain map so both planes cannot come to rank one pair of patterns
    differently. The method tie-break stays here: it is this table's alone, since
    a domain answers a path rather than a verb.
    """
    return (*rule.pattern.specificity, int(rule.method is not None))
