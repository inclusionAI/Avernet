"""Build a legacy shim by moving one parameter back into the query string.

Twenty-one of the legacy operations differ from their replacements in exactly
one way: ``bot_id`` was a required query parameter and is now a path segment.
Everything else — the other parameters, the body, the response model, the
service calls — is identical.

A second, smaller need is the mirror image: a parameter the current address
gained that the retiring one must not publish (see :func:`without_parameter`).

Writing those twenty-one shims by hand means copying twenty-one signatures,
each one a second declaration of a contract that already exists a few files
away. The copies would be correct on the day they were written and wrong the
first time somebody adds a parameter to a handler and does not think to look
here. So the signature is taken from the handler and one parameter's annotation
is swapped, which cannot fall out of step.

The result is a real function with a real signature, because that is what
FastAPI reads to build the route — not a ``**kwargs`` passthrough, which would
publish an operation with no parameters at all.
"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import Annotated, Any, Callable

from fastapi import Query, params

#: How the bot was published before it became an address: required, and
#: described as what it is rather than what it addresses.
LegacyBotIdQuery = Annotated[
    str,
    Query(description="The bot this operation addresses."),
]


def with_query_parameter(
    handler: Callable[..., Any],
    name: str,
    annotation: Any,
    *,
    replacement_default: Any = inspect.Parameter.empty,
    suffix: str = "legacy",
    doc: str | None = None,
) -> Callable[..., Any]:
    """A shim calling *handler*, with *name* re-annotated as *annotation*.

    Used to put ``bot_id`` back in the query string, and to put the skills
    group's owner parameter back under its old spelling.
    """
    # eval_str resolves the annotations against the handler's own module. Every
    # router here uses ``from __future__ import annotations``, so without it the
    # copied signature carries strings, and FastAPI — building the route from
    # this module, where those names are not in scope — cannot resolve them.
    signature = inspect.signature(handler, eval_str=True)
    if name not in signature.parameters:
        raise ValueError(f"{handler.__name__} has no parameter {name!r}")

    def rewrite(parameter: inspect.Parameter) -> inspect.Parameter:
        # A handler may declare its parameter either way: as an Annotated type
        # (no default) or as a plain type with a FastAPI marker *as* the
        # default. Re-annotating the second kind without also clearing the
        # default leaves both, which FastAPI refuses outright. So the marker is
        # unwrapped to the value it was standing in for.
        parameter_default = parameter.default
        if isinstance(parameter_default, params.Param):
            parameter_default = (
                inspect.Parameter.empty
                if parameter_default.default is Ellipsis
                else parameter_default.default
            )
        if replacement_default is not inspect.Parameter.empty:
            parameter_default = replacement_default
        return parameter.replace(annotation=annotation, default=parameter_default)

    parameters = [
        rewrite(parameter) if key == name else parameter
        for key, parameter in signature.parameters.items()
    ]

    async def shim(**kwargs: Any) -> Any:
        return await handler(**kwargs)

    # Set rather than copied via functools.wraps: wraps would leave
    # ``__wrapped__`` behind, and inspect.signature follows that to the original
    # — so FastAPI would build the route from the *new* signature and the shim
    # would publish a path parameter its address does not have.
    shim.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    shim.__name__ = f"{handler.__name__}_{suffix}"
    shim.__doc__ = doc or handler.__doc__
    return shim


def without_parameter(
    handler: Callable[..., Any],
    name: str,
    *,
    suffix: str = "legacy",
    doc: str | None = None,
) -> Callable[..., Any]:
    """A shim calling *handler* with *name* dropped from the published signature.

    The mirror of :func:`with_query_parameter`, for a parameter the retiring
    address must **not** publish at all. The handler's own default supplies the
    value, so the shim's behaviour is whatever the address did before the
    parameter existed.

    Why a retiring address would want this: a legacy route registered by
    ``relocate`` is *the same endpoint function* as its replacement, so a
    parameter added to the handler appears on both. When the addition is a new
    capability rather than a rename, publishing it on the retiring address
    widens a contract that is supposed to be frozen — and hands a caller a
    reason to stay there.
    """
    signature = inspect.signature(handler, eval_str=True)
    if name not in signature.parameters:
        raise ValueError(f"{handler.__name__} has no parameter {name!r}")

    # A handler may declare the parameter either way — ``Annotated[...] = value``
    # or a plain type with a FastAPI marker *as* the default — and only the first
    # leaves a usable value behind when the parameter is dropped. The second
    # would bind the ``Query`` object itself, so it is refused rather than
    # shipped. ``with_query_parameter`` unwraps the same two shapes.
    default = signature.parameters[name].default
    if isinstance(default, params.Param):
        default = (
            inspect.Parameter.empty if default.default is Ellipsis else default.default
        )
    if default is inspect.Parameter.empty:
        raise ValueError(
            f"{handler.__name__}.{name} has no usable default, so dropping it "
            "from the legacy signature would leave the shim unable to call the "
            "handler"
        )

    async def shim(**kwargs: Any) -> Any:
        # The value is supplied, not left to Python's own default binding. For
        # the marker form the parameter's real default *is* the ``Query``
        # object, so omitting it would hand the handler a marker instead of a
        # value — the unwrapped default above is the one that was validated.
        return await handler(**kwargs, **{name: default})

    # Same reason as ``with_query_parameter``: set, not ``functools.wraps``,
    # or ``inspect.signature`` follows ``__wrapped__`` back to the original and
    # FastAPI republishes the parameter this exists to remove.
    shim.__signature__ = signature.replace(  # type: ignore[attr-defined]
        parameters=[p for k, p in signature.parameters.items() if k != name]
    )
    shim.__name__ = f"{handler.__name__}_{suffix}"
    shim.__doc__ = doc or handler.__doc__
    return shim


def drop_parameter(
    name: str,
    rewords: Mapping[tuple[str, str], tuple[str, str]],
) -> Callable[..., Any]:
    """A ``relocate`` transform that keeps *name* off the retiring address.

    Wraps :func:`without_parameter` with the description handling every legacy
    registration needs, so it is written once rather than once per group.

    ``rewords`` maps ``(method, current_path)`` to the ``(stale, correct)`` pair
    :func:`deprecated_doc` should apply. Dropping a parameter almost always
    needs one: a handler docstring that mentions the parameter is republished at
    an address whose parameter table does not list it, which is precisely the
    contradiction ``reword`` exists to prevent. Per-operation because each
    handler describes the parameter in its own words, and ``deprecated_doc``
    matches the stale text literally — so editing a handler's docstring without
    revisiting the map fails at import instead of shipping the contradiction.

    Both halves of that promise are enforced. The stale-text half is
    ``deprecated_doc``'s. The **key** half is
    :meth:`~_DropParameter.verify_all_applied`, which the caller runs after
    ``relocate``: an entry whose ``(method, path)`` no longer matches any route
    would otherwise be skipped in silence, republishing the very contradiction
    the entry was written to remove.

    Required, not defaulted: every caller has one, and an empty map means "this
    handler never mentions the parameter", which is a claim worth writing out.
    """
    return _DropParameter(name, rewords)


class _DropParameter:
    """The transform :func:`drop_parameter` returns; see it for the contract."""

    def __init__(
        self, name: str, rewords: Mapping[tuple[str, str], tuple[str, str]]
    ) -> None:
        self._name = name
        self._rewords = rewords
        self._applied: set[tuple[str, str]] = set()

    def __call__(self, endpoint, method, new_path):
        key = (method, new_path)
        reword = self._rewords.get(key)
        if reword is not None:
            self._applied.add(key)
        return without_parameter(
            endpoint,
            self._name,
            doc=deprecated_doc(endpoint, f"{method} {new_path}", reword=reword),
        )

    def verify_all_applied(self) -> None:
        """Raise if any reword was written for a route that does not exist."""
        unused = sorted(set(self._rewords) - self._applied)
        if unused:
            raise ValueError(
                f"reword entries for {self._name!r} matched no route: {unused}. "
                "The address moved or the key is mistyped — either way the "
                "retiring address would publish a description naming a "
                "parameter it does not have."
            )


def deprecated_doc(
    handler: Callable[..., Any],
    replacement: str,
    *,
    reword: tuple[str, str] | None = None,
) -> str:
    """The handler's own description, with a line saying where to go instead.

    The published description is what an integrator reads, and the schema's
    ``deprecated`` flag alone does not say what to move to.

    **A handler docstring that says where a parameter lives cannot be shared.**
    It is written for the current address; this function republishes it on the
    retiring one, where the same sentence may be false. That is not
    hypothetical — correcting ``get_routine`` to say the bot is on the path made
    its legacy address, which still takes the bot in the query, publish the
    opposite of its own parameter table. Pass *reword* as ``(stale, correct)``
    to fix such a sentence for the retiring address. The stale text is matched
    literally and its absence raises at import, so editing the handler's
    docstring without revisiting this fails loudly instead of shipping a
    contradiction.

    Prefer not needing it: a description that says *what* a parameter is and
    lets the parameter table say *where* is correct on both addresses and needs
    no override.

    Plain prose, with no markup: this string ships to clients, and
    ``test_schema_docs`` refuses RST in the published document for exactly that
    reason — backticks that read as emphasis in source read as backticks in a
    rendered API reference.
    """
    body = inspect.getdoc(handler) or ""
    if reword is not None:
        stale, correct = reword
        if stale not in body:
            raise ValueError(
                f"{handler.__name__}: the legacy address reworded a sentence "
                f"that is no longer in the handler's docstring: {stale!r}. "
                "Update the reword, or drop it if the sentence no longer says "
                "where a parameter lives."
            )
        body = body.replace(stale, correct)
    return f"{body}\n\nDeprecated: use {replacement} instead."
