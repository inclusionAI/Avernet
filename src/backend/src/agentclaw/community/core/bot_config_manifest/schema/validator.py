"""Parse and validate a manifest document — the whole document, every rule.

Two properties this pass is built around, both W1 acceptance criteria:

* **All-or-nothing.** One unsupported category refuses the whole document and
  nothing is written. So every rule that *can* run does run, and the refusal
  carries the full list — a caller fixes their document once rather than
  discovering the next problem on every resubmission.
* **Accepted means appliable.** Everything the vocabulary can express but no
  shipped code can act on is refused here, through the same capability resolver
  ``GET …/capabilities`` answers from. The two cannot disagree because there is
  only one of them.

What this pass deliberately does **not** do: fetch anything, resolve a
credential, ask a registry whether an MCP server exists, or look at a device.
Those need either the network or a bot record, and this must be callable during
bot creation (W13) when neither is available.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import yaml

from agentclaw.community.core.bot_config_manifest.capabilities import (
    CATEGORIES,
    KIND_CATEGORY,
    KIND_SECTION,
    SECTION_SCRIPT,
    SUPPORTED_SCHEMA_VERSIONS,
    ManifestCapabilities,
)
from agentclaw.community.core.bot_config_manifest.schema._support import (
    Context,
    check_placeholders,
    duplicates,
)
from agentclaw.community.core.bot_config_manifest.schema.entries import (
    RESERVED_KEY_APPLY_ONCE,
    validate_cli_tool_entry,
    validate_identity_entry,
    validate_mcp_entry,
    validate_named_source,
    validate_resource_entry,
    validate_skill_entry,
)
from agentclaw.community.core.bot_config_manifest.schema.limits import (
    MAX_DOCUMENT_BYTES,
    MAX_ENTRIES_PER_CATEGORY,
    MAX_SCRIPT_BYTES,
)
from agentclaw.community.core.bot_config_manifest.schema.violations import (
    ManifestValidationError,
)

#: The three sections schema §1 defines, plus the version marker. Closed: an
#: unknown top-level key is refused rather than ignored.
_TOP_LEVEL_KEYS: frozenset[str] = frozenset(
    {"schema_version", "sources", "manifest", "script"}
)

#: Categories whose value is a list of entries. ``engine_config`` is the one
#: that is not — it is a single object (schema §3.4).
_LIST_CATEGORIES: frozenset[str] = frozenset(
    {"mcp", "resources", "skills", "identity", "cli_tools"}
)

#: Longest YAML parser message forwarded to a caller. The text is about their
#: own input and is worth having; an unbounded parser trace in a response body
#: is not.
_MAX_PARSE_MESSAGE = 500


class ManifestTooLargeError(ValueError):
    """The document exceeds :data:`MAX_DOCUMENT_BYTES`.

    Separate from the violation list, and answered as a 413 rather than folded
    into the 422: it is a statement about the request, not about a place in a
    document, and there is no ``location`` to name. Nothing else can be checked
    once it is true — a document too large to accept is not one to go on parsing.
    """

    def __init__(self, size_bytes: int) -> None:
        super().__init__(
            f"config manifest is {size_bytes} bytes, which exceeds the "
            f"{MAX_DOCUMENT_BYTES}-byte limit"
        )
        self.size_bytes = size_bytes
        self.limit_bytes = MAX_DOCUMENT_BYTES


class ManifestNotEncodableError(ValueError):
    """The submitted document is not encodable UTF-8.

    JSON permits an escaped lone surrogate (``"\\ud800"``) and Pydantic's ``str``
    passes it through; encoding one raises. Without this the size check turns
    caller-controlled input into a 500. Refused as a bad request instead — the
    same failure, and the same answer, as the startup script's.
    """

    def __init__(self) -> None:
        super().__init__("config manifest is not encodable as UTF-8")


@dataclass(frozen=True)
class ValidationResult:
    """What a successful validation learned about the document."""

    #: The accepted ``schema_version``. Stored on the row so an operator can
    #: count documents per version without parsing every one of them.
    schema_version: int
    #: Non-fatal notes for the ``PUT`` response. Schema §2.3 makes an unused
    #: named source a hint rather than an error — declare-then-use is allowed —
    #: so it is reported and not refused.
    warnings: tuple[str, ...]
    #: The document's parsed form. Handed back so a caller that has already
    #: validated does not parse a second time.
    parsed: dict[str, Any]


def validate_document(
    document: str, capabilities: ManifestCapabilities
) -> ValidationResult:
    """Validate a manifest document. Raises on anything short of acceptable.

    Args:
        document: The document exactly as submitted.
        capabilities: The verdicts from
            :func:`~agentclaw.community.core.bot_config_manifest.capabilities.resolve_capabilities`.
            Passed in rather than resolved here so the write path and W13's
            pre-creation preflight are provably answering from the same object.

    Raises:
        ManifestNotEncodableError: The document is not encodable UTF-8.
        ManifestTooLargeError: The document exceeds the §5 size limit.
        ManifestValidationError: Everything else, with the full list of reasons.
    """
    try:
        encoded = document.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ManifestNotEncodableError() from exc
    if len(encoded) > MAX_DOCUMENT_BYTES:
        raise ManifestTooLargeError(len(encoded))

    ctx = Context(capabilities=capabilities)
    parsed = _parse(ctx, document)
    if parsed is None:
        raise ManifestValidationError(ctx.violations)

    schema_version = _check_schema_version(ctx, parsed)
    _check_top_level_keys(ctx, parsed)
    _sweep_reserved_keys(ctx, parsed, "")
    _validate_sources(ctx, parsed.get("sources"))
    _validate_manifest(ctx, parsed.get("manifest"))
    _validate_script(ctx, parsed.get("script"))
    _collect_unused_source_warnings(ctx)

    if ctx.violations:
        raise ManifestValidationError(ctx.violations)
    return ValidationResult(
        schema_version=schema_version,
        warnings=tuple(ctx.warnings),
        parsed=parsed,
    )


def _parse(ctx: Context, document: str) -> dict[str, Any] | None:
    """YAML → mapping, or ``None`` with the reason recorded.

    ``safe_load``, never ``load``: the document is caller-authored and full
    loading constructs arbitrary Python objects from it.
    """
    try:
        parsed = yaml.safe_load(document)
    except yaml.YAMLError as exc:
        ctx.add(
            "(document)",
            "yaml_parse_error",
            f"document is not valid YAML: {str(exc)[:_MAX_PARSE_MESSAGE]}",
        )
        return None
    if parsed is None:
        ctx.add("(document)", "empty_document", "document is empty")
        return None
    if not isinstance(parsed, dict):
        ctx.add(
            "(document)",
            "invalid_document",
            "document must be a mapping with a 'schema_version' key",
        )
        return None
    return parsed


def _check_schema_version(ctx: Context, parsed: dict[str, Any]) -> int:
    """A known version, or a refusal. An unknown one is never best-effort read."""
    version = parsed.get("schema_version")
    if version is None:
        ctx.add(
            "schema_version",
            "missing_schema_version",
            "'schema_version' is required",
        )
        return 0
    if isinstance(version, bool) or not isinstance(version, int):
        ctx.add(
            "schema_version",
            "invalid_schema_version",
            "'schema_version' must be an integer",
        )
        return 0
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        ctx.add(
            "schema_version",
            "unsupported_schema_version",
            f"schema_version {version} is not supported; this build accepts "
            + ", ".join(str(v) for v in SUPPORTED_SCHEMA_VERSIONS),
        )
        return 0
    return version


def _check_top_level_keys(ctx: Context, parsed: dict[str, Any]) -> None:
    for key in parsed:
        if key not in _TOP_LEVEL_KEYS:
            ctx.add(
                key,
                "unknown_field",
                f"unknown top-level key '{key}'",
            )


def _sweep_reserved_keys(ctx: Context, node: Any, path: str) -> None:
    """Refuse ``apply_once`` wherever it appears, at any depth.

    A v1 reserved word (schema §2). Swept document-wide rather than checked per
    category because its v2 meaning is per-entry semantics that nothing here
    implements — a document carrying it today would be accepted now and mean
    something different later, which is the one outcome reserving a word is for.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key == RESERVED_KEY_APPLY_ONCE:
                ctx.add(
                    child,
                    "reserved_field",
                    "'apply_once' is reserved in v1 and cannot be written",
                )
            _sweep_reserved_keys(ctx, value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _sweep_reserved_keys(ctx, value, f"{path}[{index}]")


def _validate_sources(ctx: Context, sources: Any) -> None:
    """The top-level ``sources`` map, and the names ``from`` may resolve to."""
    if sources is None:
        return
    if not isinstance(sources, dict):
        ctx.add("sources", "invalid_sources", "'sources' must be a mapping of name to source")
        return
    for name, source in sources.items():
        if not isinstance(name, str) or not name:
            ctx.add("sources", "invalid_source_name", "a source name must be a non-empty string")
            continue
        ctx.source_names.add(name)
        if isinstance(source, dict):
            # Same sweep the entries get: a source's ``url``/``git``/``subpath``
            # take substitutions too, and a typo there fails the same way.
            for key, value in source.items():
                check_placeholders(ctx, f"sources.{name}.{key}", value)
        validate_named_source(ctx, f"sources.{name}", source)


def _validate_manifest(ctx: Context, manifest: Any) -> None:
    """The declarative half: six categories, each closed and each capability-gated."""
    if manifest is None:
        return
    if not isinstance(manifest, dict):
        ctx.add("manifest", "invalid_manifest", "'manifest' must be a mapping")
        return

    for category, value in manifest.items():
        location = f"manifest.{category}"
        if category not in CATEGORIES:
            ctx.add(
                location,
                "unknown_category",
                f"unknown manifest category '{category}'; allowed: "
                + ", ".join(CATEGORIES),
            )
            continue
        if not ctx.capabilities.supports(KIND_CATEGORY, category):
            ctx.add(
                location,
                "unsupported_category",
                f"category '{category}' is not supported: "
                + ctx.capabilities.reason_for(KIND_CATEGORY, category),
            )
            # Still walked below: a caller fixing an unsupported category should
            # not then discover a second round of shape errors inside it.
        if category == "engine_config":
            _validate_engine_config(ctx, location, value)
            continue
        _validate_category_list(ctx, location, category, value)


def _validate_engine_config(ctx: Context, location: str, value: Any) -> None:
    """One object, not a list — and ``engine_ext`` is never reachable through it.

    The platform stores ``engine_ext`` as opaque engine-owned data and promises
    never to interpret it (schema §3.4). A manifest that could write it would
    break that promise on the platform's behalf.
    """
    if not isinstance(value, dict):
        ctx.add(location, "invalid_category", "'engine_config' must be a mapping")
        return
    for key in value:
        if key != "config":
            ctx.add(
                f"{location}.{key}",
                "unknown_field",
                f"unknown field '{key}' under engine_config",
            )
    config = value.get("config")
    if config is not None and not isinstance(config, dict):
        ctx.add(f"{location}.config", "invalid_config", "'config' must be a mapping")
        return
    if isinstance(config, dict) and "engine_ext" in config:
        ctx.add(
            f"{location}.config.engine_ext",
            "engine_ext_not_writable",
            "'engine_ext' is engine-owned opaque data and can never be written "
            "through a manifest",
        )


def _validate_category_list(
    ctx: Context, location: str, category: str, value: Any
) -> None:
    """A list category: the count limit, then each entry, then cross-entry rules."""
    if not isinstance(value, list):
        ctx.add(
            location,
            "invalid_category",
            f"'{category}' must be a list of entries",
        )
        return
    if len(value) > MAX_ENTRIES_PER_CATEGORY:
        ctx.add(
            location,
            "too_many_entries",
            f"{len(value)} entries, over the {MAX_ENTRIES_PER_CATEGORY}-entry "
            f"limit for one category",
        )

    entries: list[tuple[str, dict[str, Any]]] = []
    for index, entry in enumerate(value):
        at = f"{location}[{index}]"
        if not isinstance(entry, dict):
            ctx.add(at, "invalid_entry", "an entry must be a mapping")
            continue
        entries.append((at, entry))
        _check_entry_placeholders(ctx, at, entry)

    if category == "mcp":
        for at, entry in entries:
            validate_mcp_entry(ctx, at, entry)
        _check_duplicates(
            ctx,
            location,
            [str(entry.get("server_code")) for _, entry in entries if entry.get("server_code")],
            code="duplicate_server_code",
            what="MCP server_code",
        )
    elif category == "resources":
        paths = [validate_resource_entry(ctx, at, entry) for at, entry in entries]
        _check_resource_layout(ctx, location, [p for p in paths if p])
    elif category == "skills":
        names = [validate_skill_entry(ctx, at, entry) for at, entry in entries]
        _check_duplicates(
            ctx, location, [n for n in names if n], code="duplicate_name", what="skill name"
        )
    elif category == "identity":
        for at, entry in entries:
            validate_identity_entry(
                ctx, at, entry, engine_type=ctx.capabilities.engine_type
            )
        _check_duplicates(
            ctx,
            location,
            [str(entry.get("type")) for _, entry in entries if entry.get("type")],
            code="duplicate_identity_type",
            what="identity type",
        )
    elif category == "cli_tools":
        commands = [validate_cli_tool_entry(ctx, at, entry) for at, entry in entries]
        _check_duplicates(
            ctx,
            location,
            [c for c in commands if c],
            code="duplicate_command_name",
            what="tool name",
        )


def _check_entry_placeholders(ctx: Context, location: str, entry: dict[str, Any]) -> None:
    """Scan an entry's strings for unknown ``${...}`` names.

    ``content`` is skipped: it is literal text destined for a file, and a
    knowledge base or a rules document may legitimately contain ``${...}`` that
    means something to whoever reads the file, not to this platform.
    """
    for key, value in entry.items():
        if key == "content":
            continue
        if isinstance(value, str):
            check_placeholders(ctx, f"{location}.{key}", value)
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                check_placeholders(ctx, f"{location}.{key}.{sub_key}", sub_value)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                check_placeholders(ctx, f"{location}.{key}[{index}]", item)


def _check_duplicates(
    ctx: Context, location: str, values: list[str], *, code: str, what: str
) -> None:
    for value in duplicates(values):
        ctx.add(location, code, f"duplicate {what} '{value}'")


def _check_resource_layout(ctx: Context, location: str, paths: list[str]) -> None:
    """Duplicate paths, and the nesting ban (schema §3.2).

    A directory entry claims its whole subtree — apply replaces it wholesale —
    so an entry addressed *under* another entry's directory has two owners with
    no defined precedence. Refused at write time, because at apply time the only
    honest options are to drop one silently or to fail halfway through a
    directory replacement.
    """
    _check_duplicates(ctx, location, paths, code="duplicate_path", what="resource path")
    directories = [p for p in paths if p.endswith("/")]
    for path in paths:
        for directory in directories:
            if path != directory and path.startswith(directory):
                ctx.add(
                    location,
                    "nested_resource_path",
                    f"resource path '{path}' is nested under directory entry "
                    f"'{directory}'; a directory entry owns its whole subtree",
                )
                break


def _validate_script(ctx: Context, script: Any) -> None:
    """The imperative half. Capability-gated, size-capped, and never scanned.

    ``${...}`` inside the body is **not** validated: the body is a shell script,
    the platform injects its variables as environment variables (schema §4), and
    ``${HOME}`` in a caller's script is theirs, not a typo in ours.
    """
    if script is None:
        return
    if not isinstance(script, dict):
        ctx.add("script", "invalid_script", "'script' must be a mapping with a 'body'")
        return
    for key in script:
        if key != "body":
            ctx.add(f"script.{key}", "unknown_field", f"unknown field '{key}' under script")
    body = script.get("body")
    if not isinstance(body, str):
        ctx.add("script.body", "invalid_script", "'script.body' must be a string")
        return
    if not ctx.capabilities.supports(KIND_SECTION, SECTION_SCRIPT):
        ctx.add(
            "script",
            "unsupported_script",
            "a startup script is not supported for this bot: "
            + ctx.capabilities.reason_for(KIND_SECTION, SECTION_SCRIPT),
        )
    try:
        size = len(body.encode("utf-8"))
    except UnicodeEncodeError:
        ctx.add("script.body", "invalid_script", "'script.body' is not encodable as UTF-8")
        return
    if size > MAX_SCRIPT_BYTES:
        ctx.add(
            "script.body",
            "script_too_large",
            f"script body is {size} bytes, over the {MAX_SCRIPT_BYTES}-byte limit",
        )


def _collect_unused_source_warnings(ctx: Context) -> None:
    for name in sorted(ctx.source_names - ctx.referenced_sources):
        ctx.warnings.append(
            f"source '{name}' is declared under 'sources' but never referenced "
            "by a 'from'"
        )
