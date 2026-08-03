"""Path patterns — one grammar, one ranking, for every plane that matches paths.

Two planes ask the same question of an incoming path. Route security asks which
configured rule governs ``(method, path)``; the domain map asks which configured
domain serves it. Both answer it the same way — of the patterns this path
matches, take the most specific — and a second implementation of "most specific"
is somewhere the two can come to disagree about which rule governs one request.
That disagreement is not a cosmetic one: the auth plane deciding a path is
covered by an exempt rule while the routing plane sends it somewhere else is
precisely the shape of an authorisation bypass. So there is one implementation.

The grammar is three kinds of segment:

- a **literal**, matching itself;
- a **parameter** (``{id}``), matching exactly one segment, whatever it holds;
- the **glob** (``**``), matching every remaining segment *including none*.

Callers may accept a narrower grammar than they can rank — the domain map does,
taking only ``<literals>/**`` — but they all rank through :attr:`specificity`.

No web framework here (Rule 7): this is pure matching logic.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The wildcard segment. Matches every remaining segment, and also *no*
#: remaining segment: ``/a/b/**`` matches the bare ``/a/b``. Domains rely on
#: this — ``/openapi/v1/bots/**`` has to serve ``/openapi/v1/bots`` itself.
GLOB = "**"


def split_segments(path: str) -> tuple[str, ...]:
    """*path*'s non-empty segments.

    Empty segments are dropped rather than preserved, so a leading slash, a
    trailing slash, and an accidental doubled slash all describe the same path.
    Patterns and request paths are split by this same function, so the two can
    never disagree about how many segments a path has.
    """
    return tuple(segment for segment in path.split("/") if segment)


def _is_param(segment: str) -> bool:
    """Whether *segment* is a ``{name}`` placeholder."""
    return segment.startswith("{") and segment.endswith("}")


@dataclass(frozen=True)
class PathPattern:
    """A parsed path pattern: matchable against a path, comparable to its peers."""

    segments: tuple[str, ...]

    @classmethod
    def parse(cls, pattern: str) -> PathPattern:
        """Parse ``/openapi/v1/bots/**`` into its segments."""
        return cls(segments=split_segments(pattern))

    def matches(self, path_segments: tuple[str, ...]) -> bool:
        """Whether a path — already split by :func:`split_segments` — matches."""
        return _match(self.segments, path_segments)

    @property
    def specificity(self) -> tuple[int, int, int]:
        """Higher is more specific: exact beats glob, then literals, then params.

        Compared as a tuple, so each term only breaks a tie in the one before it.
        Literals outrank parameters because a literal names one resource while a
        parameter names a shape: between ``/bots/{id}`` and ``/bots/messages``,
        the request for ``messages`` is more precisely described by the latter.

        The glob term comes first because a pattern that ends in ``**`` claims a
        whole subtree, and a pattern that claims exactly one path should win over
        one that claims everything beneath it however many literals each has.
        """
        has_glob = GLOB in self.segments
        literals = sum(
            1 for segment in self.segments if segment != GLOB and not _is_param(segment)
        )
        params = sum(1 for segment in self.segments if _is_param(segment))
        return (0 if has_glob else 1, literals, params)

    @property
    def literal_prefix(self) -> str:
        """The leading run of literal segments, as an absolute path.

        The part of the pattern that is fixed text — everything up to the first
        parameter or glob. This is what a caller needs when it has to *produce* a
        path rather than test one: a route to mount, or a prefix a raw
        (still-encoded) path must carry literally.

        A pattern beginning with a parameter or a glob has no such prefix and
        yields ``"/"``. Callers that cannot use that are expected to refuse the
        pattern when it is configured, rather than discover it here.
        """
        literals: list[str] = []
        for segment in self.segments:
            if segment == GLOB or _is_param(segment):
                break
            literals.append(segment)
        return "/" + "/".join(literals)


def _match(pattern: tuple[str, ...], segments: tuple[str, ...]) -> bool:
    """Whether *pattern* matches *segments*, one segment at a time.

    The glob returns ``True`` without consuming anything, which is what makes it
    match zero remaining segments as well as many.
    """
    if not pattern:
        return not segments
    head, rest = pattern[0], pattern[1:]
    if head == GLOB:
        return True
    if not segments:
        return False
    if head != segments[0] and not _is_param(head):
        return False
    return _match(rest, segments[1:])


__all__ = ["GLOB", "PathPattern", "split_segments"]
