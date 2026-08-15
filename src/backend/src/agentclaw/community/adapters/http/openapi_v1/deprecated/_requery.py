"""Build a legacy shim by moving one parameter back into the query string.

Twenty-one of the legacy operations differ from their replacements in exactly
one way: ``bot_id`` was a required query parameter and is now a path segment.
Everything else — the other parameters, the body, the response model, the
service calls — is identical.

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
        default = parameter.default
        if isinstance(default, params.Param):
            default = (
                inspect.Parameter.empty
                if default.default is Ellipsis
                else default.default
            )
        return parameter.replace(annotation=annotation, default=default)

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


def deprecated_doc(handler: Callable[..., Any], replacement: str) -> str:
    """The handler's own description, with a line saying where to go instead.

    The published description is what an integrator reads, and the schema's
    ``deprecated`` flag alone does not say what to move to.

    Plain prose, with no markup: this string ships to clients, and
    ``test_schema_docs`` refuses RST in the published document for exactly that
    reason — backticks that read as emphasis in source read as backticks in a
    rendered API reference.
    """
    body = inspect.getdoc(handler) or ""
    return f"{body}\n\nDeprecated: use {replacement} instead."
