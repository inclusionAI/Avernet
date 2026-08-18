"""Registration helpers for the legacy addresses.

Every route here is going away. What that costs while it stays is one thing:
somebody has to remember, on each registration, to mark it deprecated, to tag
it so a reader can see which half of the document they are in, and to record it
so the ``Deprecation``/``Sunset`` middleware stamps it. Three things to
remember is three things to forget, so :func:`legacy_route` does all of them
and :data:`LEGACY_ROUTES` is built from what it registered rather than restated
somewhere a copy can fall behind.
"""

from __future__ import annotations

import re
from typing import Any, Callable

from fastapi import APIRouter

#: Every ``(METHOD, legacy path)`` this package registers, mapped to the address
#: that replaced it. Filled in as routes are declared.
#:
#: Three things read it, which is why it is a mapping and not a list. The
#: deprecation middleware decides which responses to stamp; ``test_legacy_parity``
#: asserts every entry has a parity row; and ``admission.py`` gives each legacy
#: address the mode of the address it replaced — derived rather than restated,
#: because a hand-copied mode is a second decision about who may call, and the
#: two would eventually disagree.
LEGACY_ROUTES: dict[tuple[str, str], str] = {}


def legacy_router(prefix: str, tag: str) -> APIRouter:
    """A router for one group's legacy addresses.

    The tag carries the suffix rather than the group inventing its own, so the
    published document sorts every retiring operation together and a reader
    scanning tags sees at a glance which are on the way out.
    """
    return APIRouter(prefix=prefix, tags=[f"{tag} (deprecated)"])


def legacy_operation_id(name: str, path: str, method: str) -> str:
    """The ``operationId`` this address published *before* it was retired.

    Reproduces FastAPI's default rule — endpoint name, then the path with every
    non-word character replaced, then the method — because that is what the
    address actually published, and an ``operationId`` is part of the contract
    a generated client is written against. Generators turn it into a method
    name, so changing it renames methods in every SDK built from this document.

    That would break the compatibility promise in the one place it is least
    expected: a client that has *not* migrated, regenerating for an unrelated
    reason, would find its calls renamed. The retiring address keeps the id it
    always had; the replacement is at a different path, so FastAPI's own rule
    gives it a different one and the two cannot collide.

    The converter is stripped first: a route's ``path_format`` spells
    ``{model_id:path}`` as ``{model_id}``, and the format is what the id was
    built from. Leaving it in produced ``..._model_id_path__get`` — close
    enough to look right in review and wrong on the wire.
    """
    path_format = re.sub(r":\w+\}", "}", path)
    return re.sub(r"\W", "_", f"{name}{path_format}") + f"_{method.lower()}"


def legacy_route(
    router: APIRouter,
    method: str,
    path: str,
    endpoint: Callable[..., Any],
    replaces: str,
    **kwargs: Any,
) -> None:
    """Register *endpoint* at a legacy *path*, marked and recorded.

    ``replaces`` is the address that supersedes this one — required, because
    everything downstream (the admission mode, the parity row, the description)
    is derived from the pair rather than restated.

    ``deprecated=True`` is forced rather than defaulted: a legacy route that
    published itself as current would tell an integrator the opposite of what
    this package exists to say.

    ``operation_name`` is the *original* endpoint's function name, from which
    the address's former ``operationId`` is rebuilt — see
    :func:`legacy_operation_id`. It has to be passed rather than read off
    ``endpoint.__name__`` because a translating shim has a name of its own.
    """
    kwargs.pop("deprecated", None)
    operation_name = kwargs.pop("operation_name", None)
    if operation_name is not None:
        kwargs["operation_id"] = legacy_operation_id(
            operation_name, f"{router.prefix}{path}", method
        )
    router.add_api_route(
        path,
        endpoint,
        methods=[method.upper()],
        deprecated=True,
        **kwargs,
    )
    LEGACY_ROUTES[(method.upper(), f"{router.prefix}{path}")] = replaces
