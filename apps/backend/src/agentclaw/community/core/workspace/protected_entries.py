"""The workspace entries no resource operation may touch: the one list, kept here to be reviewed as a set.

A bot's **workspace root is the resources root**. That is the Open API's
contract (``/openapi/v1/bots/{bot_id}/resources``: every path is
workspace-relative, and no path means the root), and the apply engine's
``resources`` construct follows it: an apply that declares resources clears
the workspace and writes the declaration back.

The entries below are the exceptions, because they belong to other owners:

* the identity files at the root, written by the ``identity`` construct;
* ``skills/`` and ``skills-pool/``, written by the ``skills`` construct;
* ``state/``, ``conf/`` and ``<engine>_conf/``, the engine's own runtime
  state and configuration;
* dotfiles and dot-directories at any depth, the workspace's housekeeping.

Every surface that lists, deletes, clears or validates resources excludes
them through :func:`protected_entry_refusal`: the Open API and internal file
routes (read-only answers), the resources materialiser's listing and clear,
the Manifest validator and compiler, the genome's spec checks and the desired
state's own path rule. Change the sets here and nowhere else.
"""

from __future__ import annotations

from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES

__all__ = ["PROTECTED_ROOT_DIRS", "PROTECTED_ROOT_FILES", "protected_entry_refusal"]

#: Identity (persona) files at the workspace root: the ``identity`` construct's, never a resource.
PROTECTED_ROOT_FILES: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        "RULES.md",
        "OKR.md",
        "SAFETY.md",
        "SOUL.md",
        "OUTPUT.md",
        "MEMORY.md",
        "IDENTITY.md",
        "USER.md",
        "TOOLS.md",
        "HEARTBEAT.md",
        "BOOTSTRAP.md",
    }
)

#: Directories at the workspace root that other owners write: the skills trees (``skills`` construct), the
#: engine's state and configuration directories.
PROTECTED_ROOT_DIRS: frozenset[str] = frozenset(
    {
        "skills",
        "skills-pool",
        "state",
        "conf",
        *(f"{engine}_conf" for engine in SUPPORTED_ENGINE_TYPES),
    }
)


def protected_entry_refusal(path: str) -> str | None:
    """Why a workspace-relative path may not be a resource, or ``None`` when it may.

    ``path`` is workspace-relative as the Open API addresses it (``docs/a.md``;
    ``data/kb/`` with a trailing slash for a directory). The root itself is not
    an entry and is refused too. A protected name is protected at the root
    only: ``docs/SOUL.md`` is an ordinary resource, ``SOUL.md`` is not.
    """
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if not segments:
        return "the workspace root itself is not a resource"
    for segment in segments:
        if segment.startswith("."):
            return f"resource path {path!r} names a dotfile, which the workspace keeps for itself"
    top = segments[0]
    if len(segments) == 1 and top in PROTECTED_ROOT_FILES:
        return f"resource path {path!r} is an identity file, owned by the identity construct"
    if top in PROTECTED_ROOT_DIRS:
        return f"resource path {path!r} is under {top!r}, which is not a resources directory"
    return None
