"""The workspace entries no resource operation may touch: the one list, kept here to be reviewed as a set.

A bot's **workspace root is the resources root**. That is the Open API's
contract (``/openapi/v1/bots/{bot_id}/resources``: every path is
workspace-relative, and no path means the root), and the apply engine's
``resources`` construct follows it: an apply that declares resources clears
the workspace and writes the declaration back.

The entries below are the exceptions, because they belong to other owners:

* the identity files at the root, written by the ``identity`` construct
  (every engine's accepted types together; ``core/services/identity.py``
  narrows the set per engine);
* ``skills/`` and ``skills-pool/``, written by the ``skills`` construct;
* ``state/``, ``conf/`` and ``<engine>_conf/``, the engine's own runtime
  state and configuration;
* dotfiles and dot-directories, the workspace's housekeeping.

Two predicates, because two questions are asked. :func:`protected_entry_refusal`
answers whether a workspace-relative path may be **declared** a resource:
the names above at the root, and a dot-prefixed segment at any depth (the
Open API refuses to upload one anywhere). :func:`is_protected_root_entry`
answers what a **listing or a clear of the root** leaves alone: the root
entries above, and nothing below them — an unprotected directory goes
whole, dot-entries inside it included, exactly as the Open API's own
directory delete treats it. The Open API and internal file routes
(read-only answers), the resources materialiser's listing and clear, the
Manifest validator and compiler, the genome's spec checks and the desired
state's own path rule all read these; change the sets here and nowhere else.
"""

from __future__ import annotations

from agentclaw.community.core.workspace.constants import SUPPORTED_ENGINE_TYPES

__all__ = [
    "IDENTITY_FILE_NAMES",
    "PROTECTED_ROOT_DIRS",
    "PROTECTED_ROOT_FILES",
    "is_protected_root_entry",
    "protected_entry_refusal",
]

#: The identity (persona) file vocabulary, every engine's accepted types together: the ``identity`` construct's
#: files, at the workspace root. ``core/services/identity.py`` is its one consumer that narrows per engine
#: (``claude_code`` accepts ``CLAUDE.md`` alone); defined here so the protection rule and the validity rule
#: cannot name different sets.
IDENTITY_FILE_NAMES: frozenset[str] = frozenset(
    {
        "AGENTS.md",
        "BOOTSTRAP.md",
        "CLAUDE.md",
        "GREETING.md",
        "HEARTBEAT.md",
        "IDENTITY.md",
        "KNOWLEDGE.md",
        "MEMORY.md",
        "OKR.md",
        "OUTPUT.md",
        "README.md",
        "RULES.md",
        "SAFETY.md",
        "SOUL.md",
        "TOOLS.md",
        "USER.md",
    }
)

#: Files at the workspace root that are never resources: today exactly the identity vocabulary.
PROTECTED_ROOT_FILES: frozenset[str] = IDENTITY_FILE_NAMES

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


def is_protected_root_entry(name: str) -> bool:
    """Whether a workspace-root entry is one a root listing or clear leaves alone.

    ``name`` is the entry's name at the root (no slash). A dotfile or
    dot-directory, an identity file, or one of the system directories.
    """
    return name.startswith(".") or name in PROTECTED_ROOT_FILES or name in PROTECTED_ROOT_DIRS


def protected_entry_refusal(path: str) -> str | None:
    """Why a workspace-relative path may not be declared a resource, or ``None`` when it may.

    ``path`` is workspace-relative as the Open API addresses it (``docs/a.md``;
    ``data/kb/`` with a trailing slash for a directory). The root itself is not
    an entry and is refused too. A protected name is protected at the root
    only (``docs/SOUL.md`` is an ordinary resource, ``SOUL.md`` is not); a
    dot-prefixed segment is refused at any depth, as the Open API refuses to
    upload one.
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
