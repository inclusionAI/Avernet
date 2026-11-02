"""Minimal query-intent policy for high-availability direct retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class QueryIntent:
    """The bounded routing decision derived from a normalized query."""

    route: str
    reason: str
    target: str


QueryIntent.SEMANTIC = "semantic"
QueryIntent.DIRECT_KEYWORD = "direct_keyword"


_EXPLICIT_LOOKUP = re.compile(
    r"^(?:find|get|lookup|retrieve)\s+(?:agent|worker|bot)\s+(?P<target>.+)$"
)
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_@:.-]{0,127}$")
_IDENTIFIER_TOKEN = re.compile(r"^[a-z0-9@:][a-z0-9_.:@-]{4,127}$")


_EMBEDDED_SEPARATORS = ("_", ":", "@")


def _embedded_identifier_tokens(normalized_query: str) -> list[str]:
    """Return only repository-style identifiers embedded in a natural query.

    This is a bounded token classifier, not a domain heuristic: it only picks
    whitespace-separated ASCII tokens that are long enough and contain an
    underscore, colon, or handle separator. Natural words and CJK prose stay
    semantic even when the local snapshot is unavailable.
    """

    tokens: list[str] = []
    for token in normalized_query.split():
        if _IDENTIFIER_TOKEN.fullmatch(token) is None:
            continue
        if len(token) < 5:
            continue
        if not any(separator in token for separator in _EMBEDDED_SEPARATORS):
            continue
        tokens.append(token)
    return tokens


def resolve_query_intent(normalized_query: str) -> QueryIntent:
    """Classify bounded identifier lookups before semantic model work.

    Prose and Chinese natural-language questions remain semantic. One
    bounded ASCII token, including profile/worker identifiers, can use the
    keyword path without embedding and without sacrificing explicit lookup
    conversions.
    """

    explicit = _EXPLICIT_LOOKUP.match(normalized_query)
    if explicit is not None:
        target = explicit.group("target").strip()
        if target:
            return QueryIntent(
                route=QueryIntent.DIRECT_KEYWORD,
                reason="explicit_lookup",
                target=target,
            )

    if _IDENTIFIER.fullmatch(normalized_query) is not None:
        reason = (
            "identifier"
            if ":" in normalized_query or normalized_query.startswith("@")
            else "simple_identifier"
        )
        return QueryIntent(
            route=QueryIntent.DIRECT_KEYWORD,
            reason=reason,
            target=normalized_query,
        )

    embedded_identifiers = _embedded_identifier_tokens(normalized_query)
    if embedded_identifiers:
        return QueryIntent(
            route=QueryIntent.DIRECT_KEYWORD,
            reason="embedded_identifier",
            target=" ".join(embedded_identifiers),
        )

    return QueryIntent(
        route=QueryIntent.SEMANTIC,
        reason="natural_language",
        target=normalized_query,
    )


__all__ = ["QueryIntent", "resolve_query_intent"]
