"""Teclaw engine-relative path mapping for the device-filesystem seam.

teclaw addresses each bot's files by a path **relative to its per-bot engine
root**, under three namespaces:

- ``/workspace`` — resources (the file-manager workspace tree)
- ``/identity``  — identity files (AGENTS.md, IDENTITY.md, MEMORY.md, …)
- ``/config``    — engine config the container owns (``teclaw.json``). Unlike
  baas/arca — which keep ``openclaw.json`` in the OSS host-dir layout — the
  teclaw engine owns its config file and the backend reads/writes it per-file
  through the engine API at ``/config/teclaw.json``.

:func:`to_engine_relative` is the ``path_mapper`` injected into
``TeclawDeviceFileSystem``. Every teclaw caller — the resource read/write/mkdir
branches, the identity routes, and the promotion gather — passes a
**namespace-relative logical path** (``"workspace/<rel>"`` / ``"identity/<rel>"``),
never a host path. So this is just a normalizer: it slashes the input to
``/workspace/<rel>`` · ``/identity/<rel>``. It deliberately does NOT accept a
host path — nothing should assemble a ``/aidesktop/.../bolt_data/...`` prefix only
to strip it back off here.

The namespace constants live here (the device layer), so routers reference
``WORKSPACE_NS`` / ``IDENTITY_NS`` rather than hard-coding the strings.
"""

WORKSPACE_NS = "workspace"
IDENTITY_NS = "identity"
CONFIG_NS = "config"
# The engine config the teclaw container owns, addressed under CONFIG_NS as
# ``config/teclaw.json`` → ``/config/teclaw.json``.
TECLAW_ENGINE_CONFIG_FILE = "teclaw.json"
_NAMESPACES = (WORKSPACE_NS, IDENTITY_NS, CONFIG_NS)

# teclaw keeps user-uploaded ("local") skills flat under the workspace namespace
# at ``/workspace/skills-local/...`` — NOT nested under ``workspace/skills/`` (that
# nesting is the arca host layout). The skill_center service addresses a teclaw
# local skill by a ``skills-local/...``-relative logical path (what ``local://<…>``
# stores and what ``SkillService`` joins from its ``local_dir``); this constant +
# helper bridge that to the workspace namespace.
LOCAL_SKILLS_DIRNAME = "skills-local"


def to_engine_relative(path: str) -> str:
    """Normalize a namespace-relative logical path to its engine-relative form.

    ``"workspace/sub/x.pdf"`` → ``/workspace/sub/x.pdf``;
    ``"identity/AGENTS.md"`` → ``/identity/AGENTS.md``;
    ``"config/teclaw.json"`` → ``/config/teclaw.json``. Redundant/leading slashes
    are collapsed.

    Raises:
        ValueError: the path is not under one of the engine namespaces
            (guards against a host path or a mis-built ref reaching the engine
            seam).
    """
    parts = [p for p in path.split("/") if p]
    if parts[:1] and parts[0] in _NAMESPACES:
        return "/" + "/".join(parts)
    raise ValueError(
        f"to_engine_relative: expected a {WORKSPACE_NS}/, {IDENTITY_NS}/ or "
        f"{CONFIG_NS}/ relative path, got {path!r}"
    )


def to_local_skill_engine_path(path: str) -> str:
    """Map a teclaw local-skill logical path to its workspace namespace-relative form.

    The skill_center service addresses a teclaw local skill by a path relative to
    the bot's ``skills-local`` dir — e.g. ``"skills-local/my-skill/SKILL.md"`` (what
    ``local://<…>`` stores and what ``SkillService`` joins from ``local_dir``).
    teclaw's engine keeps those flat under the workspace namespace at
    ``/workspace/skills-local/...`` (NOT nested under ``workspace/skills/``). This
    prepends the ``workspace/`` namespace, yielding the namespace-relative form that
    :func:`to_engine_relative` (the ``TeclawDeviceFileSystem`` mapper) then turns
    into the engine-relative ``/workspace/skills-local/...``.

    ``"skills-local/my-skill/SKILL.md"`` → ``"workspace/skills-local/my-skill/SKILL.md"``.
    Idempotent if already under ``workspace/``; redundant/leading slashes and ``.``
    segments are collapsed.
    """
    parts = [p for p in path.split("/") if p and p != "."]
    if parts[:1] == [WORKSPACE_NS]:
        return "/".join(parts)
    return "/".join([WORKSPACE_NS, *parts])


__all__ = [
    "WORKSPACE_NS",
    "IDENTITY_NS",
    "CONFIG_NS",
    "TECLAW_ENGINE_CONFIG_FILE",
    "LOCAL_SKILLS_DIRNAME",
    "to_engine_relative",
    "to_local_skill_engine_path",
]
