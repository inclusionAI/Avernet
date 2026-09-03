"""One field name, one location, per operation.

A client should never be asked to fill the same thing twice. This asserts it
against the **generated document** rather than a list of known offenders, so an
operation added later that declares ``bot_id`` in the path *and* in the query
fails here instead of shipping.

Why it is worth a test of its own. The internal ``/api`` surface has 27
operations that publish one name in two locations, all of them ``bot_id``, and
nobody noticed until a reader went through the document by hand — the shape is
invisible in a diff, because the two declarations live in different files (a
route decorator and a shared dependency). The public surface has never had one,
and this is what keeps that true.

It also catches the case a document alone would hide: FastAPI keys parameters on
``(in, name)``, so two declarations of the same name *and* location silently
collapse into one published parameter. That is a different defect — a route's
own description is overwritten by whichever declaration is resolved last — so it
is checked against the dependant tree rather than the document. Both checks run
here because they are the same question asked at two depths.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from fastapi import FastAPI
from fastapi.dependencies.utils import get_flat_params

from tests.community.adapters.http.openapi_v1.conftest import public_document, public_router

#: Every place a request field can be declared. ``path`` is read off the address
#: template rather than the parameter list, so a path parameter counts even for a
#: route that (wrongly) declares it somewhere else too.
_LOCATIONS = ("path", "query", "header", "cookie", "body")


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(public_router())
    return app


def _document() -> dict[str, Any]:
    return public_document()


def _body_fields(
    document: dict[str, Any], schema: Any, seen: set[str] | None = None
) -> set[str]:
    """Top-level property names of a request-body schema, ``$ref`` resolved.

    Composition keywords are followed because a body model built with
    inheritance publishes its inherited fields under ``allOf``, and a field a
    client fills is a field a client fills whichever branch declares it.
    """
    if seen is None:
        seen = set()
    if not isinstance(schema, dict):
        return set()
    ref = schema.get("$ref")
    if ref is not None:
        if ref in seen:
            return set()
        seen.add(ref)
        name = ref.rsplit("/", 1)[-1]
        target = (document.get("components") or {}).get("schemas", {}).get(name)
        return _body_fields(document, target, seen)
    fields = set(schema.get("properties") or {})
    for keyword in ("allOf", "anyOf", "oneOf"):
        for branch in schema.get(keyword) or []:
            fields |= _body_fields(document, branch, seen)
    return fields


def _locations_by_field(document: dict[str, Any]) -> dict[str, dict[str, set[str]]]:
    """``{"METHOD /path": {"field": {"path", "query"}}}`` for every operation."""
    per_operation: dict[str, dict[str, set[str]]] = {}
    for path, item in (document.get("paths") or {}).items():
        # Applies to every operation on the path, so it has to be folded in
        # alongside each operation's own list rather than checked separately.
        shared = item.get("parameters") or []
        template = set(re.findall(r"\{([^}]+)\}", path))
        for method, operation in item.items():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            located: dict[str, set[str]] = defaultdict(set)
            for name in template:
                located[name].add("path")
            for parameter in list(operation.get("parameters") or []) + list(shared):
                if isinstance(parameter, dict) and parameter.get("name"):
                    located[parameter["name"]].add(parameter.get("in") or "?")
            request_body = operation.get("requestBody")
            if isinstance(request_body, dict):
                for media in (request_body.get("content") or {}).values():
                    for name in _body_fields(document, (media or {}).get("schema")):
                        located[name].add("body")
            per_operation[f"{method.upper()} {path}"] = dict(located)
    return per_operation


def test_no_field_is_declared_in_two_locations() -> None:
    """No operation asks a client to supply one field in two places."""
    offenders = {
        operation: {
            field: sorted(where) for field, where in fields.items() if len(where) > 1
        }
        for operation, fields in _locations_by_field(_document()).items()
    }
    offenders = {op: fields for op, fields in offenders.items() if fields}
    assert offenders == {}, (
        "these operations publish one field name in two locations, so a client "
        "is asked to fill the same value twice and the handler can only read "
        f"one of them: {offenders}"
    )


def test_every_declared_location_is_one_we_recognise() -> None:
    """Guards the check above: an unknown ``in`` would slip past it unseen."""
    seen = {
        where
        for fields in _locations_by_field(_document()).values()
        for wheres in fields.values()
        for where in wheres
    }
    assert seen <= set(_LOCATIONS), f"unrecognised parameter locations: {seen}"


def test_no_field_is_declared_twice_in_the_same_location() -> None:
    """Two declarations of one ``(location, name)`` collapse in the document.

    FastAPI keys published parameters on ``(in, name)``, so a route that
    declares ``bot_id`` as a query parameter while one of its dependencies does
    the same publishes a single parameter — carrying whichever description
    resolved last. The document cannot show this, so it is asked of the
    dependant tree.
    """
    offenders: dict[str, dict[tuple[str, str], int]] = {}
    for route in _app().routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            continue
        counts: dict[tuple[str, str], int] = defaultdict(int)
        for field in get_flat_params(dependant):
            info = getattr(field, "field_info", None)
            location = getattr(info, "in_", None)
            counts[
                (
                    str(getattr(location, "value", location)),
                    getattr(info, "alias", None) or field.name,
                )
            ] += 1
        repeated = {key: n for key, n in counts.items() if n > 1}
        if repeated:
            offenders[f"{sorted(route.methods)} {route.path}"] = repeated
    assert offenders == {}, (
        "these routes declare one parameter twice in the same location; the "
        "published document shows only the last declaration, silently "
        f"overwriting the route's own: {offenders}"
    )
