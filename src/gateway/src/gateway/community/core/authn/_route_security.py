"""Route → auth-requirement table (auth design §8).

Loads the ``route_security`` table (a ``"[METHOD ]<path-glob>" -> OR-list``
mapping) and resolves an incoming ``(method, path)`` to the **most specific**
matching rule's requirement. Fail-closed: an unmatched route resolves to
``None`` and the caller must deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from gateway.community.spi.authn import Delegation, StrategyParams

# A requirement is an OR-list of alternatives; each alternative is an AND-map of
# strategy name -> params (one strategy per alternative in practice today).
Requirement = list[dict[str, StrategyParams]]


@dataclass(frozen=True)
class _Rule:
    method: str | None  # None = applies to every method
    segments: tuple[str, ...]  # path split on "/", e.g. ("openapi", "v1", "bots", "**")
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
    requirement: Requirement = []
    for item in value or []:
        if isinstance(item, str):
            requirement.append({item: StrategyParams()})
        elif isinstance(item, dict):
            requirement.append(
                {name: _parse_params(params) for name, params in item.items()}
            )
    return requirement


def _parse_params(params: Any) -> StrategyParams:
    params = params or {}
    return StrategyParams(
        scopes=frozenset(params.get("scopes", [])),
        delegation=Delegation(params.get("delegation", Delegation.OPTIONAL.value)),
    )


# ── matching (§8.3) ──────────────────────────────────────────────────────────


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
    if head == "**":  # matches any remaining segments, including none
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
