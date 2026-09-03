"""``BotConfigArtifact`` — the published bot-config contract.

This is a **published cross-boundary contract**, not an internal model: an
external engine consumes it to boot a bot. Field names ARE the external API.
The language-neutral source of truth is ``artifact.schema.json`` (beside this
file). :data:`SCHEMA_VERSION` is distinct from the per-bot content ``version``
carried inside an artifact — and note it no longer tracks every contract change;
see the comment on the constant.

Module rules (kernel is the lowest layer):

* Import **only the standard library** — no ``agentclaw.*`` imports
  (enforced by ``tests/architecture/test_kernel_no_imports.py``).
* Pure data + (de)serialization. **No business logic.**
* ``engine_ext`` is an **opaque, engine-owned** free-form mapping. The backend
  stores / freezes / returns it verbatim and must never interpret or branch on
  its contents.
* References carry an explicit **``store``** (a key into
  :attr:`BotConfigArtifact.stores`) plus a **``path``** relative to that store
  (e.g. ``store="skill-repo"``, ``path="team/weather"``). The backend does NOT
  encode an absolute placement — the engine decides where to physically put it.
  Skills, resources and identity files all share this ``{store, path}`` shape.
  The physical store coordinates (bucket, key prefix, endpoint, region —
  **never credentials**) live once in ``stores``, keyed by ``store_id``.
  Store coordinates never carry credentials. MCP server credentials, by
  contrast, are **inlined** into the server entry (the resolved key rides in
  the endpoint query or in ``headers``) — the backend holds the plaintext at
  compose time and the engine consumes it directly, with no secret broker.
* An MCP server entry comes in two mutually exclusive forms, discriminated by
  ``transport``: a **remote** one (``http`` / ``sse``) carrying ``endpoint`` +
  ``headers``, and a **local** one (``stdio``) carrying its launch instruction
  flattened onto the entry (``command`` / ``args`` / ``env``). See
  :class:`McpServerRef`.
"""
from __future__ import annotations

from enum import StrEnum

from dataclasses import asdict, dataclass, field
from typing import Any


# Contract version. Bump on any change to the artifact shape that an external
# engine could observe. Distinct from the per-bot content ``version`` below.
# v4: MCP credentials are inlined into the server entry; the ``auth_ref``
# secret-by-reference field was removed (no container-side secret broker).
# The local (stdio) MCP launch instruction rides flat on the server entry —
# top-level ``command``/``args``/``env``, the shape MCP clients themselves
# consume (an early v4 iteration nested it under a ``stdio`` object;
# ``from_dict`` still re-flattens that form on read).
#
# The optional top-level ``cli_tools`` array (command-line tools the model can
# invoke) was added WITHOUT bumping this constant — a deliberate decision with
# the teclaw owner, 2026-08-31. The field rides into existing v4 artifacts and
# compatibility rests on the engine contract's "ignore unknown fields rather
# than reject" rule, which teclaw has agreed to
# (``engine-convergence-contract.zh-CN.md`` A5).
#
# The cost, stated so nobody rediscovers it: ``schema_version`` no longer tracks
# this contract's evolution. "Does this artifact carry cli_tools?" is answered by
# probing for the key, never by the version number.
SCHEMA_VERSION = 4

# The two values ``BotConfigArtifact.ownership`` admits (W8). Spelled once.
OWNERSHIP_PLATFORM = "platform"
OWNERSHIP_ENGINE = "engine"


class OwnershipCategory(StrEnum):
    """The categories the ``ownership`` map may name — the artifact's own
    field names, so a map key and the list it governs are spelled once."""

    MCP = "mcp"
    SKILLS = "skills"
    RESOURCES = "resources"
    IDENTITY_FILES = "identity_files"
    CLI_TOOLS = "cli_tools"


#: The category names the map may carry, as plain strings, in the enum's order.
OWNERSHIP_CATEGORIES: tuple[str, ...] = tuple(c.value for c in OwnershipCategory)


@dataclass(frozen=True)
class StoreRef:
    """Physical coordinates of a content store, referenced by ``source`` scheme.

    Holds **location only — never credentials**. A ``source`` of
    ``skill-repo://team/weather`` resolves against the store ``skill-repo`` to the
    full object path (e.g. ``oss://<bucket>/<base>/team/weather``). Store ids name
    the *store* (e.g. ``skill-repo``), not the skill ``scope`` — the two are
    orthogonal (scope = shared-vs-user; store = where the bytes live). Endpoint/region
    are optional: if absent the consuming engine supplies them from its own
    client config (the path-only ``oss://bucket/key`` convention).
    """

    type: str  # "oss" | "nas" | ...
    bucket: str | None = None  # oss bucket (store authority)
    base: str | None = None  # key prefix within the bucket / root path
    endpoint: str | None = None  # optional access endpoint
    region: str | None = None  # optional region


@dataclass(frozen=True)
class SkillRef:
    """A skill the engine should load.

    ``store`` keys into :attr:`BotConfigArtifact.stores`; ``path`` is the skill's
    location relative to that store (e.g. ``team/weather``). Physical placement
    (symlink vs. read-in-place, and where) is the engine owner's decision — the
    ref intentionally carries no layout hint.
    """

    name: str
    scope: str  # "shared" | "user"
    store: str
    path: str


@dataclass(frozen=True)
class ResourceRef:
    """A resource file the engine should make available, by ``{store, path}``."""

    name: str
    store: str
    path: str


@dataclass(frozen=True)
class CliToolRef:
    """One command-line tool the engine should expose to the model.

    **One entry = one command = one file.** ``path`` names the executable file
    itself, never a directory: the platform does the fetching, the ``sha256``
    enforcement, the unpacking of an archive form and the selection of the one
    declared file, so both source forms look identical here. The command the
    model invokes is ``name`` (uniqueness is enforced at write time, so the
    engine never arbitrates a clash), and placement — where the file lands and
    how it reaches the agent's PATH — is the engine owner's decision.

    ``md5`` is computed by the platform over the bytes at ``path`` and serves as
    the engine's **change test**, not an integrity gate: same ``md5`` as the tool
    already in the container means skip the re-download and the replace. Supply-
    chain integrity is settled platform-side by enforcing the user-declared
    ``sha256`` before delivery. It is **not** the store's ETag — a multipart
    upload's ETag is not the content MD5.
    """

    name: str
    store: str
    path: str
    md5: str
    version: str | None = None  # audit/display metadata; the engine ignores it


@dataclass(frozen=True)
class FileRef:
    """A user/platform-authored workspace file (e.g. RULES.md), by ``{store, path}``.

    Engine-*generated* files (MEMORY.md, IDENTITY.md, …) are NOT listed here —
    the engine references those via :attr:`BotConfigArtifact.engine_ext`.
    """

    name: str
    store: str
    path: str


@dataclass(frozen=True)
class McpServerRef:
    """One MCP server entry, in one of two mutually exclusive forms.

    ``transport`` is the discriminator:

    * ``"stdio"`` — a local server. Read ``command`` / ``args`` / ``env`` for
      the launch instruction; ``endpoint`` / ``headers`` are unset. Unlike a
      remote entry — which is pure data (a URL plus headers) — the launch
      instruction is an **instruction to execute**, so it is only meaningful in
      an engine whose image actually carries ``command`` at that path.
      Placement/lifecycle of the child process is the engine owner's decision;
      the backend only names what to run. It carries no credentials: a stdio
      server is a child of the engine process on the same host, so there is no
      endpoint to authenticate against.
    * ``"http"`` / ``"sse"`` — a remote server. Read ``endpoint`` and
      ``headers``; ``command`` is ``None``.

    For the remote form, resolved credentials are **inlined**: an
    ``authorization`` key rides in the ``endpoint`` query string and any secret
    header (e.g. ``x-ling-auth``) rides in ``headers`` — exactly the shape the
    device ``/api/mcp`` path produces. The backend holds the plaintext at compose
    time and the engine uses it directly; there is no secret broker /
    by-reference indirection.
    """

    # Field order is append-only: existing fields keep their positions so
    # positional construction and the ``asdict`` key order (which feeds the
    # published bytes and their content digest) stay unchanged.
    server_code: str
    name: str | None = None
    endpoint: str | None = None  # remote form
    transport: str | None = None  # discriminator, see above
    headers: dict[str, str] = field(default_factory=dict)  # remote form
    # Local form, flattened onto the entry (the shape MCP clients consume).
    # ``command`` being ``None`` is a meaningful contract state, not an "unset"
    # placeholder: a remote server genuinely has no launch instruction.
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class McpManifest:
    """The bot's resolved MCP servers."""

    servers: list[McpServerRef] = field(default_factory=list)


def _mcp_server_from_dict(data: dict[str, Any]) -> McpServerRef:
    """Rebuild one server entry from its published dict.

    A remote entry omits ``command``/``args``/``env`` entirely, so the dataclass
    defaults apply — and artifacts published before the local form existed
    deserialize unchanged. A legacy local entry (nested ``{"stdio": {...}}``,
    as pinned by snapshots from before the flat form) is re-flattened on read
    so stored artifacts stay loadable.
    """
    legacy_stdio = data.get("stdio")
    fields = {k: v for k, v in data.items() if k != "stdio"}
    if legacy_stdio:
        fields.update(legacy_stdio)
    return McpServerRef(**fields)


@dataclass(frozen=True)
class BotConfigArtifact:
    """The full, self-contained bot-config artifact an engine boots from.

    ``version`` is the per-bot content version (set for a published service-bot
    snapshot; ``None`` for a live personal/draft bot). ``schema_version`` is the
    *contract* version and is independent of it.
    """

    schema_version: int
    engine_type: str
    mcp: McpManifest = field(default_factory=McpManifest)
    skills: list[SkillRef] = field(default_factory=list)
    resources: list[ResourceRef] = field(default_factory=list)
    identity_files: list[FileRef] = field(default_factory=list)
    # ``None`` = this platform build does not produce tools yet, so the key is
    # left off the wire entirely (see ``to_dict``). It is a TRANSITIONAL state,
    # not a semantic: once the composer populates ``cli_tools`` it is always
    # present and always complete, exactly like every other category — an
    # artifact is a full snapshot of platform state, never a manifest diff. At
    # that point ``[]`` simply means "this bot has no platform-delivered tools".
    cli_tools: list[CliToolRef] | None = None
    # W8 (#1476): which categories the **platform** is asserting in this
    # artifact, and which it leaves to the engine. Category name (``mcp``,
    # ``skills``, ``resources``, ``identity_files``, ``cli_tools``) to
    # ``"platform"`` or ``"engine"``:
    #
    # * ``platform`` — the list in this artifact is the complete desired state
    #   for the category's area (engine contract §5); an empty list means
    #   "remove everything in the area".
    # * ``engine`` — the engine owns the category; it ignores the list here
    #   and keeps what it has.
    # * absent (the whole map, or one category) — the engine's pre-W8
    #   behaviour, which is what lets the map ship ahead of engine support
    #   under the ignore-unknown-fields rule (A5). ``None`` leaves the key off
    #   the wire so existing artifacts stay byte-identical (see ``to_dict``).
    #
    # The map is the wire form of the manifest's "declared / undeclared
    # category" rule (work-items §3.2): the platform marks a category
    # ``platform`` exactly when the bot's manifest declares it.
    ownership: dict[str, str] | None = None
    stores: dict[str, StoreRef] = field(default_factory=dict)
    engine_overrides: dict[str, Any] = field(default_factory=dict)
    engine_ext: dict[str, Any] = field(default_factory=dict)
    version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the published JSON shape (matches ``artifact.schema.json``).

        A remote MCP entry omits ``command``/``args``/``env`` entirely rather
        than emitting them as ``null``/empty. ``asdict`` would include the keys
        on every entry, which changes the wire shape of artifacts that contain
        no local server at all — and a consumer validating them against the
        pre-local-form definition (which is ``additionalProperties: false``)
        would reject them. Omitting them keeps those bytes exactly what they
        were, so only artifacts that genuinely carry the local form differ.

        ``cli_tools`` is omitted on the same principle: nothing populates it
        yet, and ``asdict`` would otherwise put ``"cli_tools": []`` on every
        artifact — a new key on the wire to every engine, ahead of the feature
        that gives it meaning. Omitting it keeps today's artifacts byte-identical
        to those built before the field existed. This is transitional, not a
        semantic distinction the engine has to honour: once the composer fills
        the field it is always present and always complete.
        """
        data = asdict(self)
        for server in data.get("mcp", {}).get("servers", []):
            if server.get("command") is None:
                for key in ("command", "args", "env"):
                    server.pop(key, None)
        if self.cli_tools is None:
            data.pop("cli_tools", None)
        # Same principle: an artifact that asserts no ownership carries no map,
        # so every artifact built before W8 is byte-identical on the wire.
        if self.ownership is None:
            data.pop("ownership", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotConfigArtifact":
        """Reconstruct from the published JSON shape."""
        mcp_data = data.get("mcp") or {}
        mcp = McpManifest(
            servers=[_mcp_server_from_dict(s) for s in mcp_data.get("servers", [])],
        )
        return cls(
            schema_version=data["schema_version"],
            engine_type=data["engine_type"],
            mcp=mcp,
            skills=[SkillRef(**s) for s in data.get("skills", [])],
            resources=[ResourceRef(**r) for r in data.get("resources", [])],
            identity_files=[FileRef(**f) for f in data.get("identity_files", [])],
            # Absent stays absent: reading a v4 artifact (or any artifact whose
            # manifest declared no tools) must not manufacture an empty
            # declaration, which would round-trip back out as a wipe order.
            cli_tools=(
                [CliToolRef(**t) for t in data["cli_tools"]]
                if data.get("cli_tools") is not None
                else None
            ),
            # Absent stays absent, for the reason ``cli_tools`` gives: reading
            # a pre-W8 artifact must not manufacture an ownership claim.
            ownership=(
                dict(data["ownership"]) if data.get("ownership") is not None else None
            ),
            stores={k: StoreRef(**v) for k, v in data.get("stores", {}).items()},
            engine_overrides=dict(data.get("engine_overrides", {})),
            engine_ext=dict(data.get("engine_ext", {})),
            version=data.get("version"),
        )


# A concrete, readable example of the published shape — living documentation for
# anyone consuming the contract (esp. the engine owner). Every file reference is
# ``{store, path}``: ``store`` keys into ``stores`` for the physical location,
# ``path`` is relative within that store; the engine decides actual placement.
# MCP credentials are inlined into the server entry (endpoint query / headers).
# ``stores`` holds location only — never credentials.
EXAMPLE_ARTIFACT = BotConfigArtifact(
    schema_version=SCHEMA_VERSION,
    engine_type="openclaw",
    version=7,  # a frozen published service-bot snapshot; None for a live/draft bot
    skills=[
        # shared market skill — lives in the global skills-repo store
        SkillRef(name="weather", scope="shared", store="skill-repo", path="team/weather"),
        # user-uploaded skill — it's per-bot data, so it rides the bot-data store
        # (same store as resources/identity), under the bot's skills-local subtree
        SkillRef(name="my-notes", scope="user", store="bot-data",
                 path="staff_u123/bot7/openclaw/workspace/skills/skills-local/my-notes"),
    ],
    resources=[
        # uploaded data file — under the bot's data dir in the bot-data store
        ResourceRef(
            name="sales.csv",
            store="bot-data",
            path="staff_u123/bot7/openclaw/workspace/data/sales.csv",
        ),
    ],
    identity_files=[
        # persona file — also bot-data (host-view bolt_data root, identity subtree)
        FileRef(
            name="RULES.md",
            store="bot-data",
            path="staff_u123/default/openclaw/workspace/RULES.md",
        ),
    ],
    mcp=McpManifest(
        servers=[
            # remote form — reachable over HTTP, so it carries endpoint + headers
            McpServerRef(
                server_code="github",
                name="GitHub",
                endpoint="https://mcp.example.com/github",
                transport="http",
                # resolved credential inlined as a secret header (the device-path
                # shape); an ``authorization`` key would instead ride in the
                # endpoint query (``?authorization=<token>``).
                headers={"x-ling-auth": "<resolved-token>"},
            ),
            # local form — the engine spawns it as a child process and speaks
            # JSON-RPC over its stdin/stdout. The launch instruction rides flat
            # on the entry. No endpoint, and no credential: it runs inside the
            # engine's own container as the same user.
            McpServerRef(
                server_code="hitl",
                name="HITL",
                transport="stdio",
                command="python3",
                args=["/home/admin/hitl/hitl_mcp_server.py"],
            ),
        ],
    ),
    # Physical coordinates (location only, never credentials). Both stores are OSS
    # objects in one bucket; each ref's ``path`` is relative to its store's ``base``
    # and the engine fetches ``bucket/base/path``. e.g. the resource above resolves
    # to bucket ``example-bucket`` key
    #   teclaw/prod/bolt_data/staff_u123/bot7/openclaw/workspace/data/sales.csv
    # ``skill-repo`` is the global shared skills market; ``bot-data`` is the per-bot
    # root (under the ``teclaw/{env}/`` namespace) holding resources, identity files,
    # AND user (skills-local) skills — the per-bot ``{entity}/{bot}/...`` lives in
    # each ref's path, so one static base serves every bot. (There is no per-user
    # store: this dict is built by DI and never sees a user id.)
    # NOTE: EXAMPLE only — a deployment's real bucket comes from ObjectStorageConfig.
    stores={
        "skill-repo": StoreRef(
            type="oss",
            bucket="example-bucket",
            base="aidesktop/aidesktop_prod/bolt_shared/skills-repo",
        ),
        "bot-data": StoreRef(
            type="oss",
            bucket="example-bucket",
            base="teclaw/prod/bolt_data",
        ),
    },
    engine_overrides={},
    engine_ext={"_": "opaque engine-owned blob; the backend stores it verbatim"},
)
