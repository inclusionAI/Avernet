"""The typed desired state: the apply engine's only input.

A ``DesiredState`` says, per construct, what a bot should hold. A construct
is either *declared* (a ``Declaration`` with zero or more entries) or *not
declared* (``None``): a declared-empty construct empties its area, an
undeclared one is untouched and unreported — the distinction the Manifest's
``declared_entries`` draws, carried into the type.

Every file is **stored content**, named by its digest in the content store:
a compiler has filed the bytes before it emits, and the engine never fetches.
A directory (a skill package, a directory resource) is one ``acm-tree-v1``
blob. Closed sets are enums; entries are objects; ``engine_config.config`` is
the one mapping, because its keys are undecided (G-4, #2858).

Invariants that today live in the Manifest grammar are checked here on
construction: digest form, tree paths end with ``/`` and file paths do not,
resource paths sit under :data:`RESOURCES_ROOT`, identities are unique within
a declaration. They are raised, not reported: a compiler that emits a
malformed state has a bug, which is not something an apply report should
carry.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, ClassVar, Generic, Mapping, Protocol, TypeVar

from agentclaw.community.core.apply_engine.constructs import Construct, ManifestCategory, ManifestSection
from agentclaw.community.core.apply_engine.paths import relative_path_refusal
from agentclaw.community.core.content_store import DIGEST_RE

__all__ = [
    "RESOURCES_ROOT",
    "CenterSkillVersion",
    "Declaration",
    "DesiredCliTool",
    "DesiredEngineConfig",
    "DesiredIdentityFile",
    "DesiredMcpServer",
    "DesiredResource",
    "DesiredResourceFile",
    "DesiredResourceTree",
    "DesiredScript",
    "DesiredSkill",
    "DesiredState",
    "EntryFailure",
    "LocalSkillPackage",
    "McpEndpointEnv",
    "McpHeader",
    "McpOverride",
    "McpTransport",
    "RepoSkillPath",
    "SkillOrigin",
    "StoredContent",
    "UnpackKind",
]

#: The one directory under the bot's workspace that holds its resources.
#: Declared resource paths sit under it; converging resources clears it and
#: writes the declared entries back (spec § Terms, "Resources root").
RESOURCES_ROOT = "data/"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


@dataclass(frozen=True)
class StoredContent:
    """Bytes in the content store, by address."""

    digest: str

    def __post_init__(self) -> None:
        _require(bool(DIGEST_RE.match(self.digest)), f"not a content digest: {self.digest!r}")


@dataclass(frozen=True)
class EntryFailure:
    """An entry the compiler could not compile, with the reason an apply report shows."""

    identity: str
    reason: str


class HasIdentity(Protocol):
    """What every desired entry exposes: the key the report and the materialiser address it by."""

    @property
    def identity(self) -> str: ...


E = TypeVar("E", bound=HasIdentity)


@dataclass(frozen=True)
class Declaration(Generic[E]):
    """What one construct declares: its entries, and the entries that failed to compile.

    ``Declaration(entries=())`` with no failures is "declared empty" and
    empties the area. Any failure aborts the construct the way a materialiser
    ``resolve`` failure does: the named entries fail, their neighbours are
    skipped, the area is untouched.
    """

    entries: tuple[E, ...]
    failures: tuple[EntryFailure, ...] = ()

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for entry in self.entries:
            _require(entry.identity not in seen, f"duplicate entry {entry.identity!r} in one declaration")
            seen.add(entry.identity)

    @property
    def identities(self) -> tuple[str, ...]:
        return tuple(entry.identity for entry in self.entries)


@dataclass(frozen=True)
class DesiredScript:
    """The startup script's body, after the compiler's placeholder substitution."""

    body: str
    identity: ClassVar[str] = ManifestSection.SCRIPT.value


@dataclass(frozen=True)
class DesiredIdentityFile:
    """One identity file (persona file) by its engine-legal type, e.g. ``SOUL.md``."""

    type: str
    content: StoredContent
    note: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.type), "an identity file needs a type")

    @property
    def identity(self) -> str:
        return self.type


class SkillOrigin(StrEnum):
    LOCAL = "local"
    CENTER = "center"
    REPO = "repo"


@dataclass(frozen=True)
class LocalSkillPackage:
    """A bot-owned skill: the ``acm-tree-v1`` blob of its files, ``SKILL.md`` inside."""

    tree: StoredContent


@dataclass(frozen=True)
class CenterSkillVersion:
    """A Skill Center skill at a pinned published version."""

    version: str

    def __post_init__(self) -> None:
        _require(bool(self.version), "a Skill Center skill needs a version")


@dataclass(frozen=True)
class RepoSkillPath:
    """A shared-repository skill, by its path in the repository."""

    path: str

    def __post_init__(self) -> None:
        _require(bool(self.path), "a shared-repository skill needs a path")


@dataclass(frozen=True)
class DesiredSkill:
    name: str
    source: LocalSkillPackage | CenterSkillVersion | RepoSkillPath
    note: str | None = None

    def __post_init__(self) -> None:
        _require(bool(self.name), "a skill needs a name")

    @property
    def identity(self) -> str:
        return self.name

    @property
    def origin(self) -> SkillOrigin:
        if isinstance(self.source, LocalSkillPackage):
            return SkillOrigin.LOCAL
        if isinstance(self.source, CenterSkillVersion):
            return SkillOrigin.CENTER
        return SkillOrigin.REPO


def _check_resource_path(path: str, *, tree: bool) -> None:
    refusal = relative_path_refusal(path, what="resource path")
    _require(refusal is None, refusal[1] if refusal else "")
    segments = path.rstrip("/").split("/")
    _require(all(segment not in ("", ".") for segment in segments), f"resource path {path!r} is not normalised")
    _require(path.startswith(RESOURCES_ROOT), f"resource path {path!r} is outside {RESOURCES_ROOT!r}")
    _require(len(path) > len(RESOURCES_ROOT), f"resource path {path!r} names the root itself")
    if tree:
        _require(path.endswith("/"), f"directory resource path {path!r} must end with '/'")
    else:
        _require(not path.endswith("/"), f"file resource path {path!r} must not end with '/'")


@dataclass(frozen=True)
class DesiredResourceFile:
    """One workspace file under the resources root."""

    path: str
    content: StoredContent
    note: str | None = None

    def __post_init__(self) -> None:
        _check_resource_path(self.path, tree=False)

    @property
    def identity(self) -> str:
        return self.path


@dataclass(frozen=True)
class DesiredResourceTree:
    """One directory under the resources root: its path (ending in ``/``) and its tree blob."""

    path: str
    tree: StoredContent
    note: str | None = None

    def __post_init__(self) -> None:
        _check_resource_path(self.path, tree=True)

    @property
    def identity(self) -> str:
        return self.path


DesiredResource = DesiredResourceFile | DesiredResourceTree


class McpTransport(StrEnum):
    SSE = "SSE"
    STREAMABLE_HTTP = "STREAMABLE_HTTP"


class McpEndpointEnv(StrEnum):
    PROD = "PROD"
    PRE = "PRE"


@dataclass(frozen=True)
class McpHeader:
    name: str
    value: str

    def __post_init__(self) -> None:
        _require(bool(self.name), "an MCP header needs a name")


@dataclass(frozen=True)
class McpOverride:
    """A bot's override of an MCP server's registered configuration.

    Every member is optional: an absent member means "not overridden", which
    is why each is ``None`` (or an empty tuple) rather than a default value.
    """

    url: str | None = None
    #: ``None`` = headers not overridden; ``()`` = overridden to none (clears persisted headers).
    headers: tuple[McpHeader, ...] | None = None
    endpoint_env: McpEndpointEnv | None = None
    transport: McpTransport | None = None

    def __post_init__(self) -> None:
        names = [header.name.lower() for header in self.headers or ()]
        _require(len(names) == len(set(names)), "MCP header names are case-insensitive and must be unique")

    def as_config(self) -> dict[str, Any]:
        """The mapping the MCP ports take today (``url``, ``headers``, ``endpoint_env``, ``transport_protocol``)."""
        config: dict[str, Any] = {}
        if self.url is not None:
            config["url"] = self.url
        if self.headers is not None:
            config["headers"] = {header.name: header.value for header in self.headers}
        if self.endpoint_env is not None:
            config["endpoint_env"] = self.endpoint_env.value
        if self.transport is not None:
            config["transport_protocol"] = self.transport.value
        return config

    @classmethod
    def from_config(cls, config: Mapping[str, Any]) -> McpOverride:
        """The typed override of a validated config mapping; unknown members are a compiler bug."""
        unknown = set(config) - {"url", "headers", "endpoint_env", "transport_protocol"}
        _require(not unknown, f"unknown MCP override members: {sorted(unknown)}")
        headers = config.get("headers")
        return cls(
            url=config.get("url"),
            headers=None if headers is None else tuple(McpHeader(str(n), str(v)) for n, v in headers.items()),
            endpoint_env=McpEndpointEnv(config["endpoint_env"]) if "endpoint_env" in config else None,
            transport=McpTransport(config["transport_protocol"]) if "transport_protocol" in config else None,
        )


@dataclass(frozen=True)
class DesiredMcpServer:
    server_code: str
    override: McpOverride | None = None

    def __post_init__(self) -> None:
        _require(bool(self.server_code), "an MCP server needs a server_code")

    @property
    def identity(self) -> str:
        return self.server_code


class UnpackKind(StrEnum):
    ZIP = "zip"
    TAR_GZ = "tar.gz"


@dataclass(frozen=True)
class DesiredCliTool:
    """One command-line tool: its fetched bytes, and how the install shapes them.

    ``pin`` is the declared digest when the document pinned one (OSS); ``None``
    over git, where today's convergence key is deliberately absent so the tool
    is re-installed on every apply. ``content`` is the fetched bytes either
    way.
    """

    name: str
    content: StoredContent
    pin: str | None = None
    subpath: str | None = None
    unpack: UnpackKind | None = None
    version: str | None = None
    note: str | None = None
    #: How the document named the source, echoed on the platform's row for audit; empty when it declared none.
    declared_address: str = ""

    def __post_init__(self) -> None:
        _require(bool(self.name), "a CLI tool needs a name")
        if self.pin is not None:
            _require(bool(DIGEST_RE.match(self.pin)), f"not a content digest: {self.pin!r}")

    @property
    def identity(self) -> str:
        return self.name


@dataclass(frozen=True)
class DesiredEngineConfig:
    """The ``engine_config`` mapping; no materialiser applies it yet (G-4, #2858)."""

    config: Mapping[str, Any]
    identity: ClassVar[str] = ManifestCategory.ENGINE_CONFIG.value

    def __post_init__(self) -> None:
        # A private, read-only copy: the frozen promise holds for the mapping too (it is not hashable).
        object.__setattr__(self, "config", MappingProxyType(dict(self.config)))


@dataclass(frozen=True)
class DesiredState:
    """What a bot should hold, per construct; ``None`` = not declared."""

    script: Declaration[DesiredScript] | None = None
    identity: Declaration[DesiredIdentityFile] | None = None
    resources: Declaration[DesiredResource] | None = None
    skills: Declaration[DesiredSkill] | None = None
    mcp: Declaration[DesiredMcpServer] | None = None
    engine_config: Declaration[DesiredEngineConfig] | None = None
    cli_tools: Declaration[DesiredCliTool] | None = None

    EMPTY: ClassVar[DesiredState]

    def __post_init__(self) -> None:
        if self.resources is not None:
            _check_resource_layout(self.resources.entries)

    def declaration(self, construct: Construct) -> Declaration[Any] | None:
        """The declaration for one construct, or ``None`` when the state does not declare it."""
        return getattr(self, _FIELD_BY_CONSTRUCT[construct])

    @property
    def declared(self) -> tuple[Construct, ...]:
        return tuple(c for c, name in _FIELD_BY_CONSTRUCT.items() if getattr(self, name) is not None)


def _check_resource_layout(entries: tuple[DesiredResource, ...]) -> None:
    """A directory entry owns its whole subtree, so nothing else may sit under it or share its name."""
    trees = [entry.path for entry in entries if isinstance(entry, DesiredResourceTree)]
    for entry in entries:
        for tree in trees:
            if entry.path != tree and (entry.path.startswith(tree) or entry.path + "/" == tree):
                raise ValueError(f"resource path {entry.path!r} is nested under directory entry {tree!r}")


_FIELD_BY_CONSTRUCT: dict[Construct, str] = {
    ManifestSection.SCRIPT: "script",
    ManifestCategory.IDENTITY: "identity",
    ManifestCategory.RESOURCES: "resources",
    ManifestCategory.SKILLS: "skills",
    ManifestCategory.MCP: "mcp",
    ManifestCategory.ENGINE_CONFIG: "engine_config",
    ManifestCategory.CLI_TOOLS: "cli_tools",
}

DesiredState.EMPTY = DesiredState()
