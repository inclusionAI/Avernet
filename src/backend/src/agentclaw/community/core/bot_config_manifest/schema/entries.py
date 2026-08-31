"""Per-entry rules for the five content categories and for ``mcp``.

The source-side fields (``from`` / ``subpath`` / ``source`` / ``content`` /
``auth`` / ``digest`` / ``on_fetch_failure``) are **one machine**, spelled and
defaulted identically in every category — schema §2.0 makes that a rule, so it
is implemented once here and applied per category rather than re-derived five
times. The entity-key fields (``resources.path``, ``skills.name``,
``identity.type``, ``cli_tools.name``) deliberately differ, and their rules
differ with them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentclaw.community.core.bot_config_manifest.capabilities import (
    SOURCE_CONTENT,
    SOURCE_GIT,
    SOURCE_NAMED,
    SOURCE_URL,
)
from agentclaw.community.core.bot_config_manifest.schema._support import (
    Context,
    check_digest,
    check_https_url,
    check_name,
    check_relative_path,
)
from agentclaw.community.core.bot_config_manifest.schema.limits import (
    MAX_INLINE_CONTENT_BYTES,
)

#: Engine-generated runtime state. Apply never writes them and never removes
#: them (work-items §3.2), so a document declaring one would be accepted and
#: then never converge. Refused at write time instead, which is what keeps
#: "accepted" and "appliable" the same set.
RESERVED_IDENTITY_FILES: frozenset[str] = frozenset({"MEMORY.md", "IDENTITY.md"})

#: ``skip`` is gone. Under per-entry diffing it meant "leave this one alone";
#: under category overwrite (work-items §3.2) it would mean "delete this one" —
#: the opposite of what it says. ``keep_last`` and ``fail`` cover what callers
#: reached for it to do.
VALID_ON_FETCH_FAILURE: frozenset[str] = frozenset({"keep_last", "fail"})

#: Archive forms the platform unpacks.
VALID_UNPACK: frozenset[str] = frozenset({"zip", "tar.gz"})

#: Selector for a moving ref (work-items §3.2). Declared on the **source**,
#: never on an entry: the property being described is "may this ref move under
#: me", which belongs to the thing holding the ref.
VALID_SOURCE_MODES: frozenset[str] = frozenset({"strict", "non_strict"})

#: v1 reserved word. Refused wherever it appears — as a key at any depth — so
#: that a v2 meaning cannot be silently assumed by a document written today.
RESERVED_KEY_APPLY_ONCE = "apply_once"

_COMMON_SOURCE_KEYS: frozenset[str] = frozenset(
    {"from", "subpath", "source", "content", "auth", "digest", "on_fetch_failure"}
)

#: Every key a category's entry may carry. Closed: an unknown key is refused
#: rather than ignored, because an ignored key is a caller believing they
#: configured something.
CATEGORY_ENTRY_KEYS: dict[str, frozenset[str]] = {
    "resources": _COMMON_SOURCE_KEYS | {"path", "unpack", "strip_components"},
    "skills": _COMMON_SOURCE_KEYS | {"name", "unpack"},
    "identity": _COMMON_SOURCE_KEYS | {"type"},
    "cli_tools": _COMMON_SOURCE_KEYS
    | {"name", "version", "unpack", "strip_components", "entrypoints"},
    # A registry reference, not a fetch: no source-side field applies.
    "mcp": frozenset({"server_code", "config"}),
}

#: The four mutually exclusive ways an entry can name its content (schema §2).
_SOURCE_SELECTORS = ("from", "source", "content")


@dataclass(frozen=True)
class SourceForm:
    """Which source an entry chose, once exclusivity has been settled."""

    #: One of the ``SOURCE_*`` construct names, or ``None`` when the entry
    #: named no usable source at all.
    kind: str | None
    #: True when the source is a git reference — the one form for which a
    #: ``digest`` is a contradiction rather than an option.
    is_git: bool = False


def legal_identity_types(engine_type: str) -> frozenset[str]:
    """The identity files this engine accepts.

    Imported lazily, and that is not style: ``core/services/identity.py`` pulls
    in the device dispatcher and a DI module at import time, so a module-level
    import would drag the injector into a pure validator and close a cycle. The
    constants stay defined there — one definition, per that module's own
    docstring — and ``harness/services/bot_profile.py`` reaches them the same
    way for the same reason.
    """
    from agentclaw.community.core.services.identity import (
        CLAUDE_CODE_IDENTITY_FILES,
        VALID_IDENTITY_FILES,
    )

    if engine_type == "claude_code":
        return frozenset(CLAUDE_CODE_IDENTITY_FILES)
    return frozenset(VALID_IDENTITY_FILES)


def check_entry_keys(ctx: Context, location: str, entry: dict[str, Any], category: str) -> None:
    """Refuse any key the category does not define."""
    allowed = CATEGORY_ENTRY_KEYS[category]
    for key in entry:
        if key in allowed:
            continue
        if key == RESERVED_KEY_APPLY_ONCE:
            # Reported by the document-wide reserved-word sweep, which names
            # every occurrence at any depth. Skipping it here keeps one mistake
            # from producing two violations that read like two problems.
            continue
        ctx.add(
            f"{location}.{key}",
            "unknown_field",
            f"unknown field '{key}' for a {category} entry",
        )


def resolve_source(
    ctx: Context, location: str, entry: dict[str, Any]
) -> SourceForm:
    """Settle which source an entry uses, and refuse the illegal combinations.

    Four rules, all from schema §2, and each names the offending entry:

    * **exactly one source.** ``from``/``source``/``content`` are mutually
      exclusive; two of them is an entry whose content has two origins and no
      order between them.
    * **``from`` must resolve** to a source declared under top-level ``sources``.
    * **``auth`` belongs to the source, not the entry.** With ``from`` the
      credential is declared on the named source; with ``content`` there is no
      request to authenticate.
    * **``digest`` and ``on_fetch_failure`` need a fetch.** Inline ``content``
      has none, and a git ref's commit SHA *is* its digest (§2.2), so writing
      one there is a second, weaker pin that can disagree with the first.
    """
    present = [key for key in _SOURCE_SELECTORS if key in entry]
    if len(present) > 1:
        ctx.add(
            location,
            "multiple_sources",
            "an entry names exactly one source; found "
            + ", ".join(f"'{key}'" for key in present),
        )
        return SourceForm(kind=None)
    if not present:
        ctx.add(
            location,
            "missing_source",
            "an entry must name one of 'from', 'source' or 'content'",
        )
        return SourceForm(kind=None)

    selector = present[0]
    form = _classify(ctx, location, entry, selector)

    if "auth" in entry:
        if selector == "from":
            ctx.add(
                f"{location}.auth",
                "auth_on_named_source_entry",
                "'auth' is declared on the named source, not on an entry that "
                "uses 'from'",
            )
        elif selector == "content":
            ctx.add(
                f"{location}.auth",
                "auth_on_inline_content",
                "'auth' is not valid on an inline 'content' entry — there is no "
                "request to authenticate",
            )
        elif not isinstance(entry["auth"], str) or not entry["auth"]:
            ctx.add(
                f"{location}.auth",
                "invalid_auth",
                "'auth' must name a stored credential",
            )

    if selector == "content":
        for illegal in ("digest", "on_fetch_failure"):
            if illegal in entry:
                ctx.add(
                    f"{location}.{illegal}",
                    "fetch_field_on_inline_content",
                    f"'{illegal}' is not valid on an inline 'content' entry — "
                    "nothing is fetched",
                )
        _check_inline_content(ctx, location, entry["content"])

    if "digest" in entry:
        if form.is_git:
            ctx.add(
                f"{location}.digest",
                "digest_on_git_source",
                "a git source is pinned by its commit SHA; 'digest' is not "
                "valid on one",
            )
        elif selector != "content":
            check_digest(ctx, f"{location}.digest", entry["digest"])

    if "on_fetch_failure" in entry and selector != "content":
        value = entry["on_fetch_failure"]
        if value not in VALID_ON_FETCH_FAILURE:
            ctx.add(
                f"{location}.on_fetch_failure",
                "invalid_on_fetch_failure",
                "on_fetch_failure must be 'keep_last' or 'fail'",
            )

    if "subpath" in entry:
        check_relative_path(
            ctx, f"{location}.subpath", entry["subpath"], what="subpath"
        )

    return form


def _classify(
    ctx: Context, location: str, entry: dict[str, Any], selector: str
) -> SourceForm:
    """Name the source form and gate it on what this build can resolve."""
    if selector == "content":
        ctx.require_source_support(f"{location}.content", SOURCE_CONTENT)
        return SourceForm(kind=SOURCE_CONTENT)

    if selector == "from":
        name = entry["from"]
        if not isinstance(name, str) or not name:
            ctx.add(
                f"{location}.from",
                "invalid_source_reference",
                "'from' must name a source declared under top-level 'sources'",
            )
            return SourceForm(kind=SOURCE_NAMED)
        ctx.referenced_sources.add(name)
        if name not in ctx.source_names:
            ctx.add(
                f"{location}.from",
                "undeclared_source",
                f"'from' references source '{name}', which is not declared "
                "under top-level 'sources'",
            )
        ctx.require_source_support(f"{location}.from", SOURCE_NAMED)
        # A named source may be a git source, but the reference itself is
        # already refused above in the first wave, so the git-ness of the target
        # adds nothing a caller could act on.
        return SourceForm(kind=SOURCE_NAMED)

    source = entry["source"]
    if isinstance(source, str):
        check_https_url(ctx, f"{location}.source", source)
        ctx.require_source_support(f"{location}.source", SOURCE_URL)
        return SourceForm(kind=SOURCE_URL)
    if isinstance(source, dict):
        validate_git_source(ctx, f"{location}.source", source)
        return SourceForm(kind=SOURCE_GIT, is_git=True)
    ctx.add(
        f"{location}.source",
        "invalid_source",
        "'source' must be an https URL or a git reference object",
    )
    return SourceForm(kind=None)


#: Keys a git reference object may carry, inline or under ``sources``.
_GIT_SOURCE_KEYS: frozenset[str] = frozenset({"git", "ref", "subpath", "auth", "mode"})

#: Keys a named URL source may carry.
_URL_SOURCE_KEYS: frozenset[str] = frozenset({"url", "auth", "mode"})


def validate_git_source(ctx: Context, location: str, source: dict[str, Any]) -> None:
    """Shape rules for a git reference, wherever it is written."""
    for key in source:
        if key not in _GIT_SOURCE_KEYS and key != RESERVED_KEY_APPLY_ONCE:
            ctx.add(
                f"{location}.{key}",
                "unknown_field",
                f"unknown field '{key}' on a git source",
            )
    check_https_url(ctx, f"{location}.git", source.get("git"))
    ref = source.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref):
        ctx.add(f"{location}.ref", "invalid_ref", "'ref' must be a non-empty string")
    if "subpath" in source:
        check_relative_path(
            ctx, f"{location}.subpath", source["subpath"], what="subpath"
        )
    check_source_mode(ctx, location, source)
    ctx.require_source_support(location, SOURCE_GIT)


def check_source_mode(ctx: Context, location: str, source: dict[str, Any]) -> None:
    """``mode`` is ``strict`` or ``non_strict``; anything else is refused.

    Defaulted rather than required, and ``non_strict`` is the default: someone
    who writes ``ref: main`` instead of a SHA wants the ref to move
    (work-items §3.2). The reason a misspelling is refused rather than defaulted
    is the same reason ``on_fetch_failure`` refuses one — a typo would land on
    the default silently, and the caller would believe they had pinned something.
    """
    if "mode" not in source:
        return
    if source["mode"] not in VALID_SOURCE_MODES:
        ctx.add(
            f"{location}.mode",
            "invalid_mode",
            "mode must be 'strict' or 'non_strict'",
        )


def validate_named_source(ctx: Context, location: str, source: Any) -> None:
    """One entry of the top-level ``sources`` map."""
    if not isinstance(source, dict):
        ctx.add(location, "invalid_source", "a named source must be a mapping")
        return
    has_git, has_url = "git" in source, "url" in source
    if has_git and has_url:
        ctx.add(
            location,
            "multiple_sources",
            "a named source is either a git source or a url source, not both",
        )
        return
    if has_git:
        validate_git_source(ctx, location, source)
        return
    if not has_url:
        ctx.add(
            location,
            "missing_source",
            "a named source must declare either 'git' or 'url'",
        )
        return
    for key in source:
        if key not in _URL_SOURCE_KEYS and key != RESERVED_KEY_APPLY_ONCE:
            ctx.add(
                f"{location}.{key}",
                "unknown_field",
                f"unknown field '{key}' on a url source",
            )
    check_https_url(ctx, f"{location}.url", source.get("url"))
    check_source_mode(ctx, location, source)


def _check_inline_content(ctx: Context, location: str, content: Any) -> None:
    """Inline text: a string, and inside the §5 per-entry cap."""
    if not isinstance(content, str):
        ctx.add(
            f"{location}.content",
            "invalid_content",
            "'content' must be inline UTF-8 text",
        )
        return
    try:
        size = len(content.encode("utf-8"))
    except UnicodeEncodeError:
        ctx.add(
            f"{location}.content",
            "invalid_content",
            "'content' is not encodable as UTF-8",
        )
        return
    if size > MAX_INLINE_CONTENT_BYTES:
        ctx.add(
            f"{location}.content",
            "content_too_large",
            f"inline content is {size} bytes, over the "
            f"{MAX_INLINE_CONTENT_BYTES}-byte limit",
        )


def check_unpack(
    ctx: Context, location: str, entry: dict[str, Any], *, archive_expected: bool
) -> None:
    """``unpack`` / ``strip_components`` rules, shared by resources and cli_tools.

    ``strip_components`` never auto-detects a single top-level directory
    (schema §3.2): the behaviour of a declaration must not depend on what the
    archive turns out to look like inside.
    """
    if "unpack" in entry:
        if entry["unpack"] not in VALID_UNPACK:
            ctx.add(
                f"{location}.unpack",
                "invalid_unpack",
                "unpack must be 'zip' or 'tar.gz'",
            )
        elif not archive_expected:
            ctx.add(
                f"{location}.unpack",
                "unpack_on_file_entry",
                "'unpack' applies to a directory entry; a file entry receives "
                "the fetched bytes as they are",
            )
    if "strip_components" in entry:
        value = entry["strip_components"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            ctx.add(
                f"{location}.strip_components",
                "invalid_strip_components",
                "strip_components must be a non-negative integer",
            )


def validate_mcp_entry(ctx: Context, location: str, entry: dict[str, Any]) -> None:
    """An MCP entry is a registry reference; it never carries a credential.

    Whether the ``server_code`` exists and whether the tenant may enable it are
    **apply-time** questions (schema §3.1 reuses the existing permission check),
    and deliberately not asked here: this validator answers from the engine and
    bot type alone so that W13 can run it before a bot record exists. A registry
    lookup would need a tenant-scoped service that leg does not have.
    """
    check_entry_keys(ctx, location, entry, "mcp")
    server_code = entry.get("server_code")
    if not isinstance(server_code, str) or not server_code:
        ctx.add(
            f"{location}.server_code",
            "missing_server_code",
            "an mcp entry must name a registry 'server_code'",
        )
    if "config" in entry and not isinstance(entry["config"], dict):
        ctx.add(
            f"{location}.config",
            "invalid_config",
            "'config' must be a mapping",
        )


def validate_resource_entry(
    ctx: Context, location: str, entry: dict[str, Any]
) -> str | None:
    """A workspace resource. Returns its ``path`` when the path is usable."""
    check_entry_keys(ctx, location, entry, "resources")
    path = entry.get("path")
    usable = check_relative_path(ctx, f"{location}.path", path, what="path")
    is_directory = isinstance(path, str) and path.endswith("/")
    form = resolve_source(ctx, location, entry)
    # A git or named source carries directory structure natively, so no archive
    # is involved (schema §2.2); only a URL source needs one to move a tree.
    check_unpack(
        ctx,
        location,
        entry,
        archive_expected=is_directory and form.kind == SOURCE_URL,
    )
    if is_directory and form.kind == SOURCE_URL and "unpack" not in entry:
        ctx.add(
            location,
            "missing_unpack",
            "a directory entry fetched from a URL must declare 'unpack' — HTTP "
            "has no directory semantics, so the tree travels as an archive",
        )
    return path if usable and isinstance(path, str) else None


def validate_skill_entry(ctx: Context, location: str, entry: dict[str, Any]) -> str | None:
    """A local skill. Returns its ``name`` when the name is usable."""
    check_entry_keys(ctx, location, entry, "skills")
    name = entry.get("name")
    usable = check_name(ctx, f"{location}.name", name, what="a skill name")
    form = resolve_source(ctx, location, entry)
    check_unpack(ctx, location, entry, archive_expected=True)
    # A skill contains scripts the agent loads and runs — code, not data — so
    # every non-git form must be pinned. A git ref has a commit SHA doing the
    # same job; a URL without a digest is a blind fetch of the latest at every
    # apply point (schema §3.3).
    if form.kind in (SOURCE_URL,) and "digest" not in entry:
        ctx.add(
            location,
            "missing_digest",
            "a skill fetched from a URL must declare a 'digest' — a skill is "
            "executable content and an unpinned fetch takes whatever is there "
            "at the time",
        )
    return name if usable and isinstance(name, str) else None


def validate_identity_entry(
    ctx: Context, location: str, entry: dict[str, Any], *, engine_type: str
) -> None:
    """An identity file, checked against this engine's own legal set."""
    check_entry_keys(ctx, location, entry, "identity")
    file_type = entry.get("type")
    legal = legal_identity_types(engine_type)
    if not isinstance(file_type, str) or file_type not in legal:
        ctx.add(
            f"{location}.type",
            "invalid_identity_type",
            f"identity type {file_type!r} is not valid for engine "
            f"'{engine_type}'; allowed: " + ", ".join(sorted(legal)),
        )
    elif file_type in RESERVED_IDENTITY_FILES:
        # In VALID_IDENTITY_FILES, so the check above passes it — and apply is
        # guaranteed never to write or remove it, so a document declaring it
        # would be accepted and then never converge.
        ctx.add(
            f"{location}.type",
            "reserved_identity_type",
            f"'{file_type}' is engine-generated runtime state: apply never "
            "writes it and never removes it, so a manifest declaring it could "
            "never converge",
        )
    resolve_source(ctx, location, entry)


def validate_cli_tool_entry(
    ctx: Context, location: str, entry: dict[str, Any]
) -> list[str]:
    """A command-line tool. Returns the command names it would expose."""
    check_entry_keys(ctx, location, entry, "cli_tools")
    name = entry.get("name")
    name_ok = check_name(ctx, f"{location}.name", name, what="a tool name")
    form = resolve_source(ctx, location, entry)
    check_unpack(ctx, location, entry, archive_expected="unpack" in entry)
    if "version" in entry and not isinstance(entry["version"], str):
        ctx.add(f"{location}.version", "invalid_version", "'version' must be a string")
    # Mandatory for every form: the platform is distributing an executable on a
    # caller's behalf, and the digest is also the only thing convergence can
    # compare (schema §3.7).
    if form.kind is not None and form.kind != SOURCE_GIT and "digest" not in entry:
        ctx.add(
            location,
            "missing_digest",
            "cli_tools requires a 'digest' — the platform is distributing an "
            "executable, so the supply chain is pinned or the entry is refused",
        )

    exposed: list[str] = []
    if "unpack" in entry:
        entrypoints = entry.get("entrypoints")
        if not isinstance(entrypoints, list) or not entrypoints:
            ctx.add(
                f"{location}.entrypoints",
                "missing_entrypoints",
                "an archive tool must declare 'entrypoints' — which files in "
                "the package become commands",
            )
            return exposed
        for index, entrypoint in enumerate(entrypoints):
            at = f"{location}.entrypoints[{index}]"
            # Syntax only. Whether the file exists, is a regular file, or is a
            # symlink out of the tree needs materialized content, which this
            # work item never produces — those checks belong to W9.
            if not check_relative_path(ctx, at, entrypoint, what="an entrypoint"):
                continue
            exposed.append(entrypoint.rsplit("/", 1)[-1])
        return exposed

    if name_ok and isinstance(name, str):
        exposed.append(name)
    return exposed
