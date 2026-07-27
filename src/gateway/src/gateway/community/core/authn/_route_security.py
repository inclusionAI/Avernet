"""Route → required-identity-types table (spec §8, rev 3).

Loads the ``route_security`` table (a ``"[METHOD ]<path-glob>" -> list of type
strings`` mapping) and resolves an incoming ``(method, path)`` to the **most
specific** matching rule's ``frozenset[PrincipalType]`` requirement. Fail-closed:
an unmatched route resolves to ``None`` and the caller must deny. Unknown type
strings are rejected at parse time.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gateway.community.spi.authn import PrincipalType

# A requirement is the set of identity types a route demands; the runner must
# produce one Principal of each.
Requirement = frozenset[PrincipalType]


@dataclass(frozen=True)
class _Rule:
    method: str | None  # None = applies to every method
    segments: tuple[str, ...]
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
        return cls.from_table(raw.get("route_security", {}))

    def resolve(self, method: str, path: str) -> Requirement | None:
        """Most-specific matching rule's requirement, or ``None`` if none match."""
        segments = _segments(path)
        matches = [r for r in self._rules if _matches(r, method, segments)]
        if not matches:
            return None
        return max(matches, key=_specificity).requirement


# ── parsing ──────────────────────────────────────────────────────────────────


def _parse_rule(key: str, value: Any) -> _Rule:
    method, path = _split_key(key)
    return _Rule(method=method, segments=_segments(path), requirement=_parse_req(value))


def _split_key(key: str) -> tuple[str | None, str]:
    parts = key.strip().split(None, 1)
    if len(parts) == 2 and parts[0].isupper() and parts[1].startswith("/"):
        return parts[0], parts[1]
    return None, key.strip()


def _segments(path: str) -> tuple[str, ...]:
    return tuple(seg for seg in path.split("/") if seg)


def _parse_req(value: Any) -> Requirement:
    types: set[PrincipalType] = set()
    for item in value or []:
        if not isinstance(item, str):
            raise ValueError(
                f"route requirement must be a list of type strings, got {item!r}"
            )
        types.add(_parse_type(item))
    return frozenset(types)


def _parse_type(name: str) -> PrincipalType:
    try:
        return PrincipalType(name)
    except ValueError as ex:
        raise ValueError(f"unknown identity type in route_security: {name!r}") from ex


# ── matching (spec §8.3) ─────────────────────────────────────────────────────


def _is_param(seg: str) -> bool:
    return seg.startswith("{") and seg.endswith("}")


def _matches(rule: _Rule, method: str, path_segments: tuple[str, ...]) -> bool:
    if rule.method is not None and rule.method != method:
        return False
    return _match_segments(rule.segments, path_segments)


def _match_segments(pattern: tuple[str, ...], segs: tuple[str, ...]) -> bool:
    if not pattern:
        return not segs
    head, rest = pattern[0], pattern[1:]
    if head == "**":
        return True
    if not segs:
        return False
    if head != segs[0] and not _is_param(head):
        return False
    return _match_segments(rest, segs[1:])


def _specificity(rule: _Rule) -> tuple[int, int, int, int]:
    """Higher = more specific: exact beats glob, more literals, then method."""
    has_glob = "**" in rule.segments
    literals = sum(1 for s in rule.segments if s != "**" and not _is_param(s))
    params = sum(1 for s in rule.segments if _is_param(s))
    return (0 if has_glob else 1, literals, params, int(rule.method is not None))
