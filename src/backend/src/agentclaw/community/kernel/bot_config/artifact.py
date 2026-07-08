"""``BotConfigArtifact`` — the published bot-config contract.

This is a **published cross-boundary contract**, not an internal model: an
external engine consumes it to boot a bot. Field names ARE the external API.
The language-neutral source of truth is ``artifact.schema.json`` (beside this
file); contract evolution goes through :data:`SCHEMA_VERSION` — distinct from
the per-bot content ``version`` carried inside an artifact.

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
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# Contract version. Bump on any change to the artifact shape that an external
# engine could observe. Distinct from the per-bot content ``version`` below.
# v4: MCP credentials are inlined into the server entry; the ``auth_ref``
# secret-by-reference field was removed (no container-side secret broker).
SCHEMA_VERSION = 4


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
    """One MCP server entry.

    Resolved credentials are **inlined**: an ``authorization`` key rides in the
    ``endpoint`` query string and any secret header (e.g. ``x-ling-auth``) rides
    in ``headers`` — exactly the shape the device ``/api/mcp`` path produces. The
    backend holds the plaintext at compose time and the engine uses it directly;
    there is no secret broker / by-reference indirection.
    """

    server_code: str
    name: str | None = None
    endpoint: str | None = None
    transport: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class McpManifest:
    """The bot's resolved MCP servers."""

    servers: list[McpServerRef] = field(default_factory=list)


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
    stores: dict[str, StoreRef] = field(default_factory=dict)
    engine_overrides: dict[str, Any] = field(default_factory=dict)
    engine_ext: dict[str, Any] = field(default_factory=dict)
    version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the published JSON shape (matches ``artifact.schema.json``)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BotConfigArtifact":
        """Reconstruct from the published JSON shape."""
        mcp_data = data.get("mcp") or {}
        mcp = McpManifest(
            servers=[McpServerRef(**s) for s in mcp_data.get("servers", [])],
        )
        return cls(
            schema_version=data["schema_version"],
            engine_type=data["engine_type"],
            mcp=mcp,
            skills=[SkillRef(**s) for s in data.get("skills", [])],
            resources=[ResourceRef(**r) for r in data.get("resources", [])],
            identity_files=[FileRef(**f) for f in data.get("identity_files", [])],
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
