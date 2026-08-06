"""The shared path pattern: grammar, matching, ranking, and literal prefix.

These properties are relied on by both planes, so they are pinned here once
rather than re-derived in each plane's own suite.
"""

from __future__ import annotations

import pytest

from gateway.community.core.paths import PathPattern, split_segments


def _matches(pattern: str, path: str) -> bool:
    return PathPattern.parse(pattern).matches(split_segments(path))


# ── splitting ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/openapi/v1/bots", ("openapi", "v1", "bots")),
        ("openapi/v1/bots", ("openapi", "v1", "bots")),
        ("/openapi/v1/bots/", ("openapi", "v1", "bots")),
        ("//openapi//v1//bots", ("openapi", "v1", "bots")),
        ("/", ()),
        ("", ()),
    ],
)
def test_empty_segments_are_dropped(path: str, expected: tuple[str, ...]) -> None:
    """One splitter for patterns and paths, so the two cannot disagree on count."""
    assert split_segments(path) == expected


# ── matching ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/openapi/v1/bots/abc",
        "/openapi/v1/bots/messages/x/y/z",
        "/openapi/v1/bots",  # the glob matches *no* remaining segments too
    ],
)
def test_a_glob_matches_the_whole_subtree_and_its_root(path: str) -> None:
    """`/a/b/**` serving the bare `/a/b` is what domain resolution rests on."""
    assert _matches("/openapi/v1/bots/**", path)


@pytest.mark.parametrize(
    "path",
    ["/openapi/v1/botsy", "/openapi/v1", "/openapi/v2/bots", "/api/bots"],
)
def test_a_glob_does_not_match_outside_its_prefix(path: str) -> None:
    """Segment-wise, not character-wise: `botsy` is a different segment."""
    assert not _matches("/openapi/v1/bots/**", path)


def test_a_parameter_matches_exactly_one_segment() -> None:
    assert _matches("/openapi/v1/bots/{id}", "/openapi/v1/bots/42")
    assert not _matches("/openapi/v1/bots/{id}", "/openapi/v1/bots/42/skills")
    assert not _matches("/openapi/v1/bots/{id}", "/openapi/v1/bots")


def test_an_exact_pattern_matches_only_itself() -> None:
    assert _matches("/openapi/v1/bots", "/openapi/v1/bots")
    assert not _matches("/openapi/v1/bots", "/openapi/v1/bots/42")


# ── ranking ──────────────────────────────────────────────────────────────────


def _rank(pattern: str) -> tuple[int, int, int]:
    return PathPattern.parse(pattern).specificity


def test_more_literals_beat_fewer() -> None:
    """The rule the socket prefix depends on, on both planes.

    `/openapi/v1/bots/messages/**` must outrank `/openapi/v1/bots/**` — for
    routing, so the socket domain wins its own prefix; and for route security,
    so the socket's empty requirement wins over the bots user requirement.
    """
    assert _rank("/openapi/v1/bots/messages/**") > _rank("/openapi/v1/bots/**")


def test_an_exact_pattern_beats_a_glob_with_more_literals() -> None:
    """A pattern claiming one path beats one claiming a whole subtree."""
    assert _rank("/openapi/v1") > _rank("/openapi/v1/bots/messages/**")


def test_a_literal_beats_a_parameter_at_the_same_depth() -> None:
    """`/bots/messages` names a resource; `/bots/{id}` names a shape."""
    assert _rank("/openapi/v1/bots/messages") > _rank("/openapi/v1/bots/{id}")


def test_the_root_glob_ranks_lowest_of_all() -> None:
    assert _rank("/**") < _rank("/openapi/**") < _rank("/openapi/v1/**")


# ── literal prefix ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "expected"),
    [
        ("/openapi/v1/bots/**", "/openapi/v1/bots"),
        ("/openapi/v1/bots/messages/**", "/openapi/v1/bots/messages"),
        ("/openapi/v1/bots", "/openapi/v1/bots"),
        ("/openapi/v1/bots/{id}/skills", "/openapi/v1/bots"),
    ],
)
def test_the_literal_prefix_stops_at_the_first_wildcard(
    pattern: str, expected: str
) -> None:
    """What a caller needs when it must *produce* a path, not test one."""
    assert PathPattern.parse(pattern).literal_prefix == expected


@pytest.mark.parametrize("pattern", ["/**", "/{id}/x"])
def test_a_pattern_with_no_literal_head_has_no_prefix(pattern: str) -> None:
    """Callers that cannot use `/` are expected to refuse such a pattern at
    configuration time rather than discover it here."""
    assert PathPattern.parse(pattern).literal_prefix == "/"
