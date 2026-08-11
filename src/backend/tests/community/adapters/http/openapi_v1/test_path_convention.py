"""The addressing rule for the whole ``/openapi/v1/bots`` surface.

Every operation is addressed ``/openapi/v1/bots/<component>/…``: the
component's **literal** name first, and a bot-scoped operation takes
``{bot_id}`` as the first segment after it. The ``bots`` component is the one
exception — it *is* the component the base names, so it owns
``/openapi/v1/bots`` and ``/openapi/v1/bots/{bot_id}``, with its own
sub-resources beneath the bot.

These assertions run against the **generated document** rather than a
hand-maintained list of addresses, so a route added later that breaks the rule
fails here instead of in review. See ``openapi_v1/__init__.py`` for why the rule
exists.
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI

from agentclaw.community.adapters.http.openapi_v1 import (
    PUBLIC_API_PREFIX,
    build_public_router,
)

_BASE = f"{PUBLIC_API_PREFIX}/bots"

#: The docs' copy of the reserved names, fenced under this anchor. Parsed rather
#: than duplicated here: the point of the check is that the docs and the routes
#: cannot drift, which a second hardcoded list would not give.
_README = Path(__file__).resolve().parents[5] / "docs" / "openapi-v1" / "README.md"
_RESERVED_ANCHOR = "<!-- reserved-component-names -->"

#: Names claimed in the docs *before* any route publishes them — currently only
#: where the gateway serves the address on another plane. Kept as its own list
#: rather than folded into the one above, so the equality check there stays an
#: equality check: a documented name with no route is otherwise indistinguishable
#: from docs that have fallen behind the routes.
_UNROUTED_ANCHOR = "<!-- reserved-component-names-unrouted -->"


def _paths() -> list[str]:
    app = FastAPI()
    app.include_router(build_public_router())
    return list(app.openapi()["paths"])


def _segments(path: str) -> list[str]:
    """*path*'s segments below the ``/openapi/v1/bots`` base."""
    assert path == _BASE or path.startswith(f"{_BASE}/"), path
    return [s for s in path[len(_BASE) :].split("/") if s]


def _components() -> set[str]:
    """Every literal occupying the first segment under the base.

    Derived from the routes, not declared, so it cannot fall behind them.
    """
    return {
        segments[0]
        for path in _paths()
        if (segments := _segments(path)) and not segments[0].startswith("{")
    }


def test_every_path_lives_under_the_bots_base():
    """The gateway resolves by the segment after the version base.

    A path outside ``/openapi/v1/bots`` would route to a different upstream —
    or to none — and the mistake is invisible until deploy.
    """
    offenders = [p for p in _paths() if p != _BASE and not p.startswith(f"{_BASE}/")]
    assert not offenders, f"paths outside {_BASE}: {offenders}"


def test_no_path_repeats_bot_before_the_id():
    """``/openapi/v1/bots/identity/bot/{bot_id}`` said "bot" twice.

    The base already says it; a second ``bot`` segment told a reader nothing and
    no other component had one.
    """
    offenders = [p for p in _paths() if "/bot/" in p]
    assert not offenders, f"redundant '/bot/' segment: {offenders}"


def test_no_component_hides_behind_the_bot_id_wildcard():
    """A component's own name must precede ``{bot_id}``, never follow it.

    ``/openapi/v1/bots/{bot_id}/status`` is fine — ``status`` is a bots-owned
    sub-resource of the bot record. ``/openapi/v1/bots/{bot_id}/sessions`` is
    not: ``sessions`` is a component, so the path claims the bots component
    serves something another module owns.
    """
    components = _components()
    offenders = [
        p
        for p in _paths()
        if (segments := _segments(p))
        and segments[0].startswith("{")
        and len(segments) > 1
        and segments[1] in components
    ]
    assert not offenders, f"component name behind the wildcard: {offenders}"


def test_only_the_bots_component_owns_the_bare_wildcard():
    """Exactly one path may open with a parameter, and it is the bot itself."""
    wildcards = {
        segments[0]
        for p in _paths()
        if (segments := _segments(p)) and segments[0].startswith("{")
    }
    assert wildcards <= {"{bot_id}"}, f"unexpected top-level parameters: {wildcards}"


def test_channels_is_gone():
    """Deleted rather than left as a stub — it was *published*.

    An unimplemented component a caller cannot distinguish from an implemented
    one is worse than an absent one: it 500s on every call.
    """
    offenders = [p for p in _paths() if "channels" in p]
    assert not offenders, f"channels was removed: {offenders}"


def _fenced_names(anchor: str) -> set[str]:
    """The first fenced block following *anchor* in the README, as a name set."""
    text = _README.read_text(encoding="utf-8")
    anchored = text.split(anchor, 1)
    assert len(anchored) == 2, f"{anchor} missing from {_README}"
    fenced = re.search(r"```text\n(.*?)```", anchored[1], re.DOTALL)
    assert fenced is not None, f"no fenced block after {anchor}"
    return set(fenced.group(1).split())


def _documented_reserved_names() -> set[str]:
    return _fenced_names(_RESERVED_ANCHOR)


def _documented_unrouted_names() -> set[str]:
    return _fenced_names(_UNROUTED_ANCHOR)


def test_the_docs_reserved_names_match_the_routes():
    """A bot whose id equals a component name is unreachable at that address.

    That is the cost of the bots component keeping the bare
    ``/openapi/v1/bots/{bot_id}``, and it is only acceptable while the set is
    written down. Deriving one side from the routes is what stops the docs
    quietly falling behind a component added later.
    """
    assert _documented_reserved_names() == _components()


def test_names_reserved_ahead_of_their_routes_are_not_routed():
    """A reserved name that gains a route must move to the routed list.

    The two lists are asserted disjoint rather than merged, because they are
    reserved for different reasons and only one of them can be checked against
    the routes. Merging would force the equality check above to be relaxed to a
    subset check, and that check is the only thing stopping the docs from
    quietly falling behind a component added later.
    """
    overlap = _documented_unrouted_names() & _components()
    assert not overlap, (
        f"{sorted(overlap)} now has routes — move it to the routed reserved list"
    )


def test_the_socket_prefix_is_reserved():
    """`messages` is claimed by the gateway on the socket plane.

    Recorded here because nothing in *this* service publishes it: an HTTP
    request to `/openapi/v1/bots/messages/ws/...` still arrives here, so without
    the reservation a bot could take the id and the intended component could
    never have the address.
    """
    assert "messages" in _documented_unrouted_names()
