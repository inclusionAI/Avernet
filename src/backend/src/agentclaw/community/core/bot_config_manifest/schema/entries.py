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
    ManifestCategory,
    SourceForm,
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
from agentclaw.community.core.bot_config_manifest.schema.sources import (
    SourceDecl,
    parse_source,
)
from agentclaw.community.core.bot_config_manifest.support_matrix import (
    ARCHIVE_FIELDS,
    SourceKind,
    archive_field_refusal,
    digest_required,
    refusal_for,
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

#: Which (category, protocol) combinations this build accepts is no longer
#: written here at all: it is
#: :data:`~agentclaw.community.core.bot_config_manifest.support_matrix.MATRIX`,
#: one table that this validator and the capabilities endpoint both read. The
#: per-category narrowing that used to live at this spot was the defect, not the
#: rule — it could not be enumerated, published, or tested cell by cell.

#: v1 reserved word. Refused wherever it appears — as a key at any depth — so
#: that a v2 meaning cannot be silently assumed by a document written today.
RESERVED_KEY_APPLY_ONCE = "apply_once"

_COMMON_SOURCE_KEYS: frozenset[str] = frozenset(
    {"from", "subpath", "source", "content", "auth", "digest", "on_fetch_failure"}
)

#: Every key a category's entry may carry. Closed: an unknown key is refused
#: rather than ignored, because an ignored key is a caller believing they
#: configured something.
CATEGORY_ENTRY_KEYS: dict[ManifestCategory, frozenset[str]] = {
    ManifestCategory.RESOURCES: _COMMON_SOURCE_KEYS
    | {"path", "unpack", "strip_components"},
    ManifestCategory.SKILLS: _COMMON_SOURCE_KEYS | {"name", "unpack"},
    ManifestCategory.IDENTITY: _COMMON_SOURCE_KEYS | {"type"},
    ManifestCategory.CLI_TOOLS: _COMMON_SOURCE_KEYS | {"name", "version", "unpack"},
    # A registry reference, not a fetch: no source-side field applies.
    #
    # ``config`` was here and is deliberately gone. manifest-schema §3.1 defined
    # it as per-bot configuration "the same shape as the existing MCP config
    # API" — but that API writes ``ac_user_mcp_config``, keyed
    # ``(user_id, server_code)``, and its write calls
    # ``sync_mcp_detail_to_all_bots``. Materialising a declared ``config`` would
    # therefore make applying ONE bot's manifest change MCP configuration for
    # EVERY bot its owner has: a blast radius no other category has, and one
    # §3.2's per-category area rule does not sanction. Its payload is also
    # ``api_key`` and ``custom_headers``, which design §4.5 forbids a manifest
    # from carrying at all.
    #
    # What is genuinely per-bot is the enabled-server set
    # (``ac_bot_mcp_installation``) — exactly what §3.2 names as this category's
    # area, and exactly what apply converges. So a v1 entry is a bare
    # ``server_code``, and ``config`` is refused by the ``unknown_field`` path
    # below the same way the retired ``cli_tools.entrypoints`` is.
    ManifestCategory.MCP: frozenset({"server_code"}),
}

#: The four mutually exclusive ways an entry can name its content (schema §2).
_SOURCE_SELECTORS = ("from", "source", "content")


@dataclass(frozen=True)
class EntrySource:
    """Which source an entry chose, once exclusivity has been settled.

    Two vocabularies, deliberately both kept. ``form`` is *how the entry was
    spelled* and remains the published ``constructs`` axis. ``kind`` is *which
    protocol the content travels by* and is what every support rule keys on —
    a ``from:`` naming a git source is ``NAMED``/``GIT``, and the rules that
    used to read ``form`` there are the ones that got D5 wrong.
    """

    #: The chosen form, or ``None`` when the entry named no usable source.
    form: SourceForm | None
    #: The protocol, or ``None`` when it could not be determined (an undeclared
    #: ``from`` name, a malformed source object). A ``None`` kind means every
    #: protocol-keyed rule below stays silent: the entry is already refused for
    #: a reason the caller has to fix first, and a second verdict derived from a
    #: guess would send them at the wrong field.
    kind: SourceKind | None = None
    #: The resolved declaration, for the roads that have one. Inline ``content``
    #: and an inline ``source:`` URL string have no declaration object.
    decl: SourceDecl | None = None

    @property
    def is_git(self) -> bool:
        """The one protocol whose commit SHA already is the entry's pin."""
        return self.kind is SourceKind.GIT


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


def check_entry_keys(
    ctx: Context, location: str, entry: dict[str, Any], category: ManifestCategory
) -> None:
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
            f"unknown field '{key}' for a {category.value} entry",
        )


def resolve_source(
    ctx: Context,
    location: str,
    entry: dict[str, Any],
    category: ManifestCategory,
) -> EntrySource:
    """Settle which source an entry uses, and refuse the illegal combinations.

    Five rules, all from schema §2, and each names the offending entry:

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
    * **the (category, protocol) cell must be open.** Asked of
      :data:`~agentclaw.community.core.bot_config_manifest.support_matrix.MATRIX`
      — the same table ``GET …/capabilities`` publishes — so a refusal here and
      the answer a caller reads before writing are the same string, and the
      combination is refused at ``PUT`` rather than at apply.
    """
    present = [key for key in _SOURCE_SELECTORS if key in entry]
    if len(present) > 1:
        ctx.add(
            location,
            "multiple_sources",
            "an entry names exactly one source; found "
            + ", ".join(f"'{key}'" for key in present),
        )
        return EntrySource(form=None)
    if not present:
        ctx.add(
            location,
            "missing_source",
            "an entry must name one of 'from', 'source' or 'content'",
        )
        return EntrySource(form=None)

    selector = present[0]
    source = _classify(ctx, location, entry, selector)
    if source.kind is not None:
        refusal = refusal_for(category, source.kind)
        if refusal is not None:
            # The cell's own code and message, verbatim. Nothing here rewords
            # or prefixes them: the capabilities endpoint publishes the same
            # object, and a caller comparing the two must see one string.
            ctx.add(
                _refusal_location(location, selector, source),
                refusal.code,
                refusal.reason,
            )
            return EntrySource(form=None)

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
        if source.is_git:
            ctx.add(
                f"{location}.digest",
                "digest_on_git_source",
                "a git source is pinned by its commit SHA; 'digest' is not "
                "valid on one",
            )
        elif selector != "content":
            check_digest(ctx, f"{location}.digest", entry["digest"])
    elif source.kind is not None and digest_required(category, source.kind):
        # Keyed on the PROTOCOL, which is the whole of the D5 fix: reaching a
        # git source by name used to classify as ``NAMED`` and be charged this
        # rule, demanding a pin that git bytes cannot meaningfully carry.
        ctx.add(
            location,
            "missing_digest",
            f"a {category.value} entry fetched over '{source.kind.value}' must "
            "declare a 'digest' — the platform is distributing executable "
            "content, and an unpinned fetch takes whatever is there at the time",
        )

    if "on_fetch_failure" in entry and selector != "content":
        value = entry["on_fetch_failure"]
        if value not in VALID_ON_FETCH_FAILURE:
            ctx.add(
                f"{location}.on_fetch_failure",
                "invalid_on_fetch_failure",
                "on_fetch_failure must be 'keep_last' or 'fail'",
            )

    if "subpath" in entry:
        # Valid on every protocol, and composed with the source's own subpath
        # at fetch time (source's first, then the entry's). This is what lets
        # one declared source serve many entries — without it a git source
        # addresses exactly one file and every second entry needs a duplicate
        # source block carrying the same url, ref and auth.
        check_relative_path(
            ctx, f"{location}.subpath", entry["subpath"], what="subpath"
        )

    return source


def _refusal_location(
    location: str, selector: str, source: EntrySource
) -> str:
    """Where to hang a matrix refusal.

    Inline ``content`` is refused *at the content field* — the caller wrote the
    bytes right there and that is what has to go. Every other form is refused at
    the entry, because the fix is usually the category or the source, not the
    one key naming it.
    """
    if source.kind is SourceKind.CONTENT and selector == "content":
        return f"{location}.content"
    return location


def _classify(
    ctx: Context, location: str, entry: dict[str, Any], selector: str
) -> EntrySource:
    """Name the source form, resolve its protocol, and gate it on this build.

    Three roads reach a protocol. Inline ``content`` *is* one. An inline
    ``source:`` string is an object over https, so ``oss``. Everything else —
    an inline ``source:`` object, or a ``from:`` name — is a declaration, and
    :func:`~agentclaw.community.core.bot_config_manifest.schema.sources.parse_source`
    is the only thing that reads one.
    """
    if selector == "content":
        ctx.require_source_support(f"{location}.content", SourceForm.CONTENT)
        return EntrySource(form=SourceForm.CONTENT, kind=SourceKind.CONTENT)

    if selector == "from":
        name = entry["from"]
        if not isinstance(name, str) or not name:
            ctx.add(
                f"{location}.from",
                "invalid_source_reference",
                "'from' must name a source declared under top-level 'sources'",
            )
            return EntrySource(form=SourceForm.NAMED)
        ctx.referenced_sources.add(name)
        if name not in ctx.source_names:
            ctx.add(
                f"{location}.from",
                "undeclared_source",
                f"'from' references source '{name}', which is not declared "
                "under top-level 'sources'",
            )
            return EntrySource(form=SourceForm.NAMED)
        ctx.require_source_support(f"{location}.from", SourceForm.NAMED)
        # The named source's protocol IS this entry's protocol — that identity
        # is the point of the axis. ``sources`` is walked before ``manifest``,
        # so a declaration that parsed is already here; one that did not is
        # ``None``, its own violations already recorded at the source, and this
        # entry stays silent rather than blaming it a second time.
        decl = ctx.sources.get(name)
        if decl is None:
            return EntrySource(form=SourceForm.NAMED)
        return EntrySource(form=SourceForm.NAMED, kind=decl.protocol, decl=decl)

    source = entry["source"]
    if isinstance(source, str):
        # A bare https URL: one object, over https, with no ref. That is
        # ``oss`` — the protocol covers a public CDN file and a private bucket
        # alike, and the credential (or its absence) is the only difference.
        check_https_url(ctx, f"{location}.source", source)
        ctx.require_source_support(f"{location}.source", SourceForm.URL)
        return EntrySource(form=SourceForm.URL, kind=SourceKind.OSS)
    if isinstance(source, dict):
        decl = validate_source_declaration(ctx, f"{location}.source", source)
        if decl is None:
            return EntrySource(form=None)
        form = SourceForm.GIT if decl.is_git else SourceForm.URL
        ctx.require_source_support(f"{location}.source", form)
        return EntrySource(form=form, kind=decl.protocol, decl=decl)
    ctx.add(
        f"{location}.source",
        "invalid_source",
        "'source' must be an https URL or a source object declaring "
        "'protocol' and 'url'",
    )
    return EntrySource(form=None)


def validate_source_declaration(
    ctx: Context, location: str, source: Any
) -> SourceDecl | None:
    """Shape rules for a source declaration, wherever it is written.

    The pure parser owns the vocabulary (which protocols exist, which keys each
    one takes, what replaces the old spelling); this wrapper contributes the two
    checks that need document context — the https/userinfo/length rule on the
    URL and the workspace-path rule on ``subpath`` — plus ``Context`` reporting.
    One declaration, one parser, whether it was written inline on an entry or
    under top-level ``sources``.
    """
    decl, violations = parse_source(source)
    for violation in violations:
        ctx.add(location + violation.suffix, violation.code, violation.message)
    if decl is None:
        return None
    check_https_url(ctx, f"{location}.url", decl.url)
    if decl.subpath is not None:
        check_relative_path(
            ctx, f"{location}.subpath", decl.subpath, what="subpath"
        )
    return decl


def validate_named_source(
    ctx: Context, location: str, name: str, source: Any
) -> None:
    """One entry of the top-level ``sources`` map.

    The parsed declaration is recorded on the context under its name so an
    entry's ``from:`` can resolve to a protocol. That resolution is what keeps
    ``from:`` from being a third column in the support matrix: a named source
    declares a protocol, and the protocol's cell is the one that governs.
    """
    decl = validate_source_declaration(ctx, location, source)
    if decl is not None:
        ctx.sources[name] = decl


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
    ctx: Context,
    location: str,
    entry: dict[str, Any],
    *,
    source: EntrySource,
    archive_expected: bool,
) -> None:
    """``unpack`` / ``strip_components`` rules, shared by resources and cli_tools.

    **The protocol gate comes first.** Both fields describe an archive, and an
    archive is an ``oss`` idea: on git the platform holds a real tree and
    ``subpath`` selects within it, so there is nothing to unpack. Declaring
    either against a git source is refused rather than silently ignored — a
    field that appears to configure something and does nothing is the failure
    mode this whole change exists to remove, and it is the one Appendix C's
    ``cli_tools`` trap and defect D3 both were.

    ``strip_components`` never auto-detects a single top-level directory
    (schema §3.2): the behaviour of a declaration must not depend on what the
    archive turns out to look like inside.
    """
    if source.kind is not None:
        refused = False
        for field in sorted(ARCHIVE_FIELDS & entry.keys()):
            refusal = archive_field_refusal(source.kind, field)
            if refusal is not None:
                ctx.add(
                    f"{location}.{field}", "archive_field_on_source", refusal
                )
                refused = True
        if refused:
            # The shape checks below would report the same two fields again,
            # under a rule that no longer applies to them.
            return

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


def check_source_subpath(
    ctx: Context,
    location: str,
    entry: dict[str, Any],
    *,
    source: EntrySource,
    archive_expected: bool,
) -> None:
    """``subpath`` must have something to select *within*.

    On ``git`` it always does: the source delivers a tree and ``subpath`` names
    a path in it. On ``oss`` the source delivers one object, so ``subpath``
    selects a member of an **archive** — and where no archive is unpacked there
    is nothing for it to select. Writing it there configures nothing.

    That is the exact failure this change exists to remove, so it is refused at
    ``PUT`` rather than ignored at apply. Two shapes reached it: a resources or
    identity entry naming one object (no archive at all), and a ``cli_tools``
    entry with no ``unpack`` — which the acquisition already refused, but only
    once the apply was running.
    """
    if "subpath" not in entry:
        return
    if source.kind is not SourceKind.OSS or archive_expected:
        return
    ctx.add(
        f"{location}.subpath",
        "subpath_without_archive",
        "'subpath' selects a member of an archive, and this entry unpacks "
        "none — an 'oss' source delivers one object, addressed by the source's "
        "own 'url'. Declare 'unpack' if the object is an archive, point the "
        "source's 'url' at the object you want, or use 'protocol: git', where "
        "'subpath' selects within the repository's tree",
    )


def validate_mcp_entry(ctx: Context, location: str, entry: dict[str, Any]) -> None:
    """An MCP entry is a registry reference; it never carries a credential.

    Whether the ``server_code`` exists and whether the tenant may enable it are
    **apply-time** questions (schema §3.1 reuses the existing permission check),
    and deliberately not asked here: this validator answers from the engine and
    bot type alone so that W13 can run it before a bot record exists. A registry
    lookup would need a tenant-scoped service that leg does not have.
    """
    check_entry_keys(ctx, location, entry, ManifestCategory.MCP)
    server_code = entry.get("server_code")
    if not isinstance(server_code, str) or not server_code:
        ctx.add(
            f"{location}.server_code",
            "missing_server_code",
            "an mcp entry must name a registry 'server_code'",
        )


def validate_resource_entry(
    ctx: Context, location: str, entry: dict[str, Any]
) -> str | None:
    """A workspace resource. Returns its ``path`` when the path is usable."""
    check_entry_keys(ctx, location, entry, ManifestCategory.RESOURCES)
    path = entry.get("path")
    usable = check_relative_path(ctx, f"{location}.path", path, what="path")
    is_directory = isinstance(path, str) and path.endswith("/")
    source = resolve_source(ctx, location, entry, ManifestCategory.RESOURCES)
    # Git carries directory structure natively, so a tree needs no packaging
    # (schema §2.2); only an object store does, because one HTTPS GET fetches
    # one object and a tree has to travel inside it.
    archive_expected = is_directory and source.kind is SourceKind.OSS
    check_unpack(
        ctx, location, entry, source=source, archive_expected=archive_expected
    )
    check_source_subpath(
        ctx, location, entry, source=source, archive_expected=archive_expected
    )
    if is_directory and source.kind is SourceKind.OSS and "unpack" not in entry:
        ctx.add(
            location,
            "missing_unpack",
            "a directory entry fetched over 'oss' must declare 'unpack' — one "
            "request fetches one object, so the tree travels as an archive; "
            "over 'protocol: git' the tree arrives as a tree and no 'unpack' "
            "is needed",
        )
    return path if usable and isinstance(path, str) else None


def validate_skill_entry(ctx: Context, location: str, entry: dict[str, Any]) -> str | None:
    """A local skill. Returns its ``name`` when the name is usable."""
    check_entry_keys(ctx, location, entry, ManifestCategory.SKILLS)
    name = entry.get("name")
    usable = check_name(ctx, f"{location}.name", name, what="a skill name")
    source = resolve_source(ctx, location, entry, ManifestCategory.SKILLS)
    check_unpack(ctx, location, entry, source=source, archive_expected=True)
    # A skills entry over ``oss`` always unpacks — a skill IS a package — so
    # ``subpath`` always has an archive to select within.
    check_source_subpath(
        ctx, location, entry, source=source, archive_expected=True
    )
    # Two rules used to live here and now do not. That a skill cannot be inline
    # text is the ``(skills, content)`` cell of the support matrix; that a skill
    # fetched over ``oss`` must carry a digest is ``DIGEST_REQUIRED``. Both are
    # enforced by ``resolve_source`` from the same table the capabilities
    # endpoint publishes, so this category no longer holds a private copy of
    # either — which is how the digest rule came to read the wrong axis (D5).
    return name if usable and isinstance(name, str) else None


def validate_identity_entry(
    ctx: Context, location: str, entry: dict[str, Any], *, engine_type: str
) -> None:
    """An identity file, checked against this engine's own legal set."""
    check_entry_keys(ctx, location, entry, ManifestCategory.IDENTITY)
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
    source = resolve_source(ctx, location, entry, ManifestCategory.IDENTITY)
    # An identity file is one text body and is never an archive, so on ``oss``
    # there is nothing a ``subpath`` could select within.
    check_source_subpath(
        ctx, location, entry, source=source, archive_expected=False
    )


def validate_cli_tool_entry(
    ctx: Context, location: str, entry: dict[str, Any]
) -> str | None:
    """A command-line tool. Returns its ``name`` — the command it exposes.

    **One entry is one command is one file** (schema §3.7). An earlier draft
    made an entry "a directory plus a list of files inside it to expose", and
    that shape has been flattened: two commands in one archive are two entries,
    each pointing at its own file with ``subpath``. The flattening removed a
    whole rule set that existed only to constrain it — in-package traversal,
    symlink escape, basename collisions across a bot's tools — so none of it is
    implemented here either. What remains is the ordinary source machinery plus
    the two rules below.
    """
    check_entry_keys(ctx, location, entry, ManifestCategory.CLI_TOOLS)
    name = entry.get("name")
    name_ok = check_name(ctx, f"{location}.name", name, what="a tool name")
    source = resolve_source(ctx, location, entry, ManifestCategory.CLI_TOOLS)
    check_unpack(
        ctx, location, entry, source=source, archive_expected="unpack" in entry
    )
    # The acquisition refuses a subpath with no 'unpack' — but only once the
    # apply is running. Asked here instead: the surface must not accept what
    # apply will reject.
    check_source_subpath(
        ctx, location, entry, source=source, archive_expected="unpack" in entry
    )
    if "version" in entry and not isinstance(entry["version"], str):
        ctx.add(f"{location}.version", "invalid_version", "'version' must be a string")
    # The mandatory digest is ``DIGEST_REQUIRED[(cli_tools, oss)]``, applied by
    # ``resolve_source``. It used to be applied here against the source *form*,
    # which is what made ``from:`` a git source demand a pin git bytes cannot
    # meaningfully carry, and then fail at apply anyway (D5). Keyed on the
    # protocol, reaching a git source by name and writing one inline are the
    # same thing, because they are.
    return name if name_ok and isinstance(name, str) else None
