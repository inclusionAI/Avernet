"""The workspace-relative path rule, owned by the engine that writes the paths.

Pure (no context, no I/O). The Manifest schema asks it at ``PUT`` and the
desired-state model asks it on construction, so the two sides cannot drift:
a path the engine will not write is refused before a document is stored, and
a compiler that emits one has a bug.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["PATH_CHARS_RE", "relative_path_refusal", "resource_path_refusal"]

#: Conservative on purpose: letters, digits, and the four separators a real
#: workspace path needs, plus the ``${...}`` placeholder characters a Manifest
#: source key may carry. Everything a shell, a URL or a filesystem would have
#: to quote is refused here rather than escaped at the write sites.
PATH_CHARS_RE = re.compile(r"^[A-Za-z0-9._/\-${}]+$")


def relative_path_refusal(value: Any, *, what: str = "path") -> tuple[str, str] | None:
    """``(code, message)`` for a value the workspace cannot accept, or ``None`` when it is usable.

    ``..`` is checked **per segment**, not as a substring: a directory named
    ``..config`` is legitimate and contains the two characters. Normalisation
    is not asked here: a fetch ``key`` or ``subpath`` is an address in
    someone else's namespace (an object-store key may contain ``//``), and
    only a path the engine writes must be normalised, see
    :func:`resource_path_refusal`.
    """
    if not isinstance(value, str) or not value:
        return "invalid_path", f"{what} must be a non-empty string"
    if value.startswith("/") or (len(value) > 1 and value[1] == ":"):
        return "absolute_path", f"{what} must be workspace-relative, not absolute"
    if value.startswith("~"):
        return "absolute_path", f"{what} must not start with '~'"
    segments = value.split("/")
    if any(segment == ".." for segment in segments):
        return "path_traversal", f"{what} must not contain a '..' segment"
    if not PATH_CHARS_RE.match(value):
        return (
            "invalid_path",
            f"{what} may only contain letters, digits, '.', '_', '-', '/' and ${{...}} placeholders",
        )
    return None


def resource_path_refusal(value: Any, *, what: str = "resource path") -> tuple[str, str] | None:
    """:func:`relative_path_refusal` plus normalisation, for a path the engine writes under the workspace.

    A resource path is where the engine puts a file, so it is one spelling:
    no empty segment (``a//b``) and no ``.`` segment (``a/./b``); one
    trailing ``/`` is allowed, since it marks a directory entry. The Manifest
    schema asks this for ``resources[].path`` at ``PUT``, the Manifest
    compiler re-asks it per entry, and the desired-state model asks it on
    construction, so a path the schema accepts never fails the model.
    """
    refusal = relative_path_refusal(value, what=what)
    if refusal is not None:
        return refusal
    core = value[:-1] if value.endswith("/") else value
    if any(segment in ("", ".") for segment in core.split("/")):
        return "invalid_path", f"{what} {value!r} is not normalised: an empty or '.' segment"
    return None
