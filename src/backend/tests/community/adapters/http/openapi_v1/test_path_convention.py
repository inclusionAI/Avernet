"""The addressing rule for the whole ``/openapi/v1/bots`` surface.

Every bot-scoped operation is addressed ``/openapi/v1/bots/{bot_id}/<component>/…``:
the bot first, the component's literal name after it. The operations that
address no single bot — creating a bot, listing them, the tenant-wide reads —
keep a literal in the segment a bot id is otherwise read from, and they are the
only things that do.

These assertions run against the **generated document** rather than a
hand-maintained list of addresses, so a route added later that breaks the rule
fails here instead of in review. They are asserted over the *current* contract
only: the retiring addresses are component-first by definition, and a rule
asserted over the addresses this API used to have could only be satisfied by
never having changed them. See ``openapi_v1/__init__.py`` for why the rule
exists and ``openapi_v1/deprecated/`` for what still answers at the old shape.
"""

from __future__ import annotations

import re
from pathlib import Path


from agentclaw.community.adapters.http.openapi_v1 import (
    PUBLIC_API_PREFIX,
)
from tests.community.adapters.http.openapi_v1.conftest import public_document

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


def _document() -> dict:
    return public_document()


def _paths() -> list[str]:
    """Every published address — the current ones and the retiring ones."""
    return [
        path
        for path in _document()["paths"]
        if path == _BASE or path.startswith(f"{_BASE}/")
    ]


def _current_paths() -> list[str]:
    """Only the addresses that are part of the current contract.

    The surface answers at two sets of addresses while callers migrate. The
    addressing rule below is about the shape this API *has*; asserting it over
    the addresses it *had* could only be satisfied by never having changed
    them.
    """
    document = _document()
    return [
        path
        for path, item in document["paths"].items()
        if path == _BASE or path.startswith(f"{_BASE}/")
        if any(
            isinstance(operation, dict)
            and "responses" in operation
            and not operation.get("deprecated", False)
            for operation in item.values()
        )
    ]


def _segments(path: str) -> list[str]:
    """*path*'s segments below the ``/openapi/v1/bots`` base."""
    assert path == _BASE or path.startswith(f"{_BASE}/"), path
    return [s for s in path[len(_BASE) :].split("/") if s]


#: The groups that address no single bot, so they keep a literal in the segment
#: a bot id is otherwise read from. Everything else is ``{bot_id}``-first.
#:
#: ``logs`` is the one worth knowing about: it takes ``bot_id``, but as a filter
#: over a tenant-level trace query rather than an address, and three of its five
#: operations have no bot dimension at all. Forcing it under ``{bot_id}`` would
#: remove the ability to query across bots.
_BOT_FREE = frozenset(
    {
        "all",
        "authorized",
        "ceiling",
        "check-name",
        "catalog",
        "loadtest",
        "local",
        "logs",
        "market",
        "metadata",
        "mcp",
        # `routines/all` is the owner-level aggregate: it lists the named
        # user's fleet across every bot, so the first segment names the
        # component namespace, not one bot — the same literal the retiring
        # routines shim already keeps there.
        "routines",
        "spaces",
        # Repo catalog is tenant-wide but follows the Skill namespace as
        # requested by its public contract: /bots/skills/repository/... .
        # It therefore does not name one concrete Bot in this segment.
        "skills",
        # Tenant source credentials (W3, #1471): a tenant-level object — it
        # shares the bots namespace the bots domain routes, but never
        # names one bot, exactly like the other literal groups above.
        "source-credentials",
        # Creating a bot with its manifest (W13, #1696): a creation, so there is
        # no bot to address yet — the same shape as `POST /openapi/v1/bots`,
        # which needs no entry here only because it adds no segment at all. Its
        # status poll *is* `{bot_id}`-first and needs nothing here.
        "with-manifest",
        "work-order-notifications",
        "work-orders",
    }
)


def _components() -> set[str]:
    """Every literal occupying the first segment under the base.

    Derived from the routes, not declared, so it cannot fall behind them.
    """
    return {
        segments[0]
        for path in _paths()
        if (segments := _segments(path)) and not segments[0].startswith("{")
    }


def test_bot_path_selection_lives_under_the_bots_base():
    """The gateway resolves by the segment after the version base.

    A path outside ``/openapi/v1/bots`` would route to a different upstream —
    or to none — and the mistake is invisible until deploy.
    """
    assert _paths(), "no bot paths found on the public surface"
    assert all(path == _BASE or path.startswith(f"{_BASE}/") for path in _paths())


def test_no_path_repeats_bot_before_the_id():
    """``/openapi/v1/bots/identity/bot/{bot_id}`` said "bot" twice.

    The base already says it; a second ``bot`` segment told a reader nothing and
    no other component had one.
    """
    offenders = [p for p in _paths() if "/bot/" in p]
    assert not offenders, f"redundant '/bot/' segment: {offenders}"


def test_every_bot_scoped_operation_names_the_bot_first():
    """An operation that acts on one bot is addressed by that bot, first.

    This is the inverse of what this test used to assert, and the reversal is
    the point of ``specs/2026-08-15-openapi-v1-bot-first-addressing``. The old
    rule put each component's literal ahead of ``{bot_id}``, which meant every
    component name occupied the segment a bot id is read from — fifteen names a
    bot could never be called. Under bot-first only the operations with no
    single bot keep a literal there.

    A current path therefore either opens with ``{bot_id}`` or opens with one
    of the literals in :func:`_bot_free_components`. There is no third shape.
    """
    offenders = [
        path
        for path in _current_paths()
        if (segments := _segments(path))
        and not segments[0].startswith("{")
        and segments[0] not in _BOT_FREE
    ]
    assert not offenders, (
        "these operations neither address a bot first nor belong to a group "
        f"that addresses no single bot: {offenders}"
    )


def test_only_the_bots_component_owns_the_bare_wildcard():
    """Exactly one path may open with a parameter, and it is the bot itself."""
    wildcards = {
        segments[0]
        for p in _paths()
        if (segments := _segments(p)) and segments[0].startswith("{")
    }
    assert wildcards <= {"{bot_id}"}, f"unexpected top-level parameters: {wildcards}"


def test_channels_uses_only_bot_first_addresses():
    """The restored Channels contract is real and follows Bot-first addressing."""
    channel_paths = {p for p in _paths() if "channels" in p}
    assert channel_paths == {
        "/openapi/v1/bots/{bot_id}/channels",
        "/openapi/v1/bots/{bot_id}/channels/{channel_id}",
        "/openapi/v1/bots/{bot_id}/channels/{channel_id}/status",
    }


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
