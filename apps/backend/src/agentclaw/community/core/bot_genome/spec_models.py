"""The hashed half of a revision as classes: ``GenomeSpec`` and ``GenomePolicy`` (contract §4, §5).

One set of models serves the core and the HTTP surface, so there is no
second copy of the contract to drift: the shape (fields, which are
required, the closed value sets) is written out here, the value patterns
and length limits are read from the contract's own schema file at import
(:mod:`contract`), and ``tests/community/core/bot_genome/test_contract_models.py``
round-trips every contract example through these classes.

``from_json`` / ``to_json`` are the two doors. ``to_json`` gives exactly the
contract's JSON object, which is what gets canonicalised and hashed; the
revision id of a parsed example is therefore the example's own. A member
the contract makes optional (an MCP override, its fields, a
``skill.update``'s two halves) is left out when absent and refused when
sent as ``null``, as the schema refuses it; a member the contract makes
nullable (``memory``, ``script``, a deleted skill file) keeps its ``null``.
The registry still
runs the contract schema on that JSON before recording, as the contract's
oracle for the rules a type cannot carry (list order, key uniqueness, the
engine-specific persona set).
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agentclaw.community.core.bot_genome import contract

__all__ = [
    "CenterSkill",
    "ContractModel",
    "Digest",
    "GenePath",
    "GenomePolicy",
    "GenomeSpec",
    "LocalSkill",
    "McpConfig",
    "McpServerEntry",
    "MemoryItem",
    "MemoryMode",
    "MemorySpec",
    "PersonaFile",
    "PersonaFileType",
    "RepoSkill",
    "ResourceEntry",
    "RiskTier",
    "ScriptEntry",
    "SkillEntry",
    "ToolsSpec",
    "constrained",
    "optional_member",
    "schema_property",
]


def schema_property(schema_file: str, *path: str | int) -> dict[str, Any]:
    """One node of a contract schema, e.g. ``schema_property(REVISION_SCHEMA_FILE, "definitions", "digest")``."""
    revision, patch = contract.schemas()
    node: Any = revision if schema_file == contract.REVISION_SCHEMA_FILE else patch
    for part in path:
        node = node[part]
    return node


def constrained(schema_file: str, *path: str | int) -> type[str]:
    """``str`` carrying the ``pattern`` / ``minLength`` / ``maxLength`` of one contract schema node."""
    node = schema_property(schema_file, *path)
    return Annotated[  # type: ignore[return-value]
        str,
        StringConstraints(pattern=node.get("pattern"), min_length=node.get("minLength"), max_length=node.get("maxLength")),
    ]


_REV = contract.REVISION_SCHEMA_FILE


def optional_member(description: str, **constraints: Any) -> Any:
    """A member the contract makes optional and not nullable: left out when absent, refused as ``null``.

    The attribute is ``None`` at runtime while absent (the annotation names the
    present type, so the OpenAPI document shows no ``null``); readers check
    ``is not None`` before using it.
    """
    return Field(default=None, exclude_if=lambda value: value is None, description=description, **constraints)


#: ``sha256:<64 hex>``: the address of stored bytes (contract §3.3).
Digest = constrained(_REV, "definitions", "digest")
#: A gene path in the contract's grammar (README §2): a category, then percent-encoded segments.
GenePath = constrained(_REV, "definitions", "genePath")
#: Contract §6.3 approval tiers, lowest to highest.
RiskTier = Literal["T0", "T1", "T2", "T3"]
#: The identity file types the Manifest accepts; which ones a given bot's engine accepts is narrower (README §4.2).
PersonaFileType = Literal[
    "AGENTS.md", "BOOTSTRAP.md", "CLAUDE.md", "GREETING.md", "HEARTBEAT.md", "KNOWLEDGE.md", "OKR.md",
    "OUTPUT.md", "README.md", "RULES.md", "SAFETY.md", "SOUL.md", "TOOLS.md", "USER.md",
]
#: How apply writes the curated items into the engine's memory (README §4.4).
MemoryMode = Literal["seed", "merge", "replace"]
SkillName = constrained(_REV, "definitions", "skillName")
ResourcePath = constrained(_REV, "definitions", "resourcePath")
MemoryKey = constrained(_REV, "definitions", "memoryKey")
EvidenceRef = constrained(_REV, "definitions", "evidenceRef")
CenterVersion = constrained(_REV, "definitions", "skillEntry", "oneOf", 1, "properties", "center_version")
RepoPath = constrained(_REV, "definitions", "skillEntry", "oneOf", 2, "properties", "repo_path")
McpUrl = constrained(_REV, "definitions", "mcpConfig", "properties", "url")
HeaderName = constrained(_REV, "definitions", "mcpConfig", "properties", "headers", "propertyNames")
HeaderValue = constrained(_REV, "definitions", "mcpConfig", "properties", "headers", "additionalProperties")
MemoryText = constrained(_REV, "definitions", "memoryItem", "properties", "text")


class ContractModel(BaseModel):
    """A contract record: no extra members, the contract's own (lookaround) patterns, two doors in and out."""

    model_config = ConfigDict(extra="forbid", regex_engine="python-re")

    @classmethod
    def from_json(cls, value: Any) -> Self:
        """Parse the contract's JSON object; raises ``pydantic.ValidationError`` naming every violation."""
        return cls.model_validate(value)

    def to_json(self) -> dict[str, Any]:
        """The contract's JSON object, the shape that is canonicalised and hashed."""
        return self.model_dump(mode="json")


# ---------------------------------------------------------------------------------------------
# Spec entries


class PersonaFile(ContractModel):
    """One identity file of the bot, keyed by type; its text is stored by digest."""

    type: PersonaFileType = Field(description="Which identity file; at most one entry per type.")
    digest: Digest = Field(description="The file's bytes, valid UTF-8, in the content store.")


class LocalSkill(ContractModel):
    """A bot-owned skill whose files are stored as one acm-tree-v1 tree."""

    name: SkillName = Field(description="The skill's name and key; equals the SKILL.md metadata name.")
    origin: Literal["local"] = Field(description="Marks this shape: a bot-owned skill.")
    digest: Digest = Field(description="acm-tree-v1 of the skill's files; must contain SKILL.md.")


class CenterSkill(ContractModel):
    """A governed Skill Center skill, pinned by version and not copied."""

    name: SkillName = Field(description="The skill's name in Skill Center, and its key.")
    origin: Literal["center"] = Field(description="Marks this shape: a Skill Center skill.")
    center_version: CenterVersion = Field(description="The published Skill Center version this revision uses.")


class RepoSkill(ContractModel):
    """A skill supplied from the shared skill repository, by repository path; neither copied nor pinned."""

    name: SkillName = Field(description="The skill's name as the bot sees it, and its key.")
    origin: Literal["repo"] = Field(description="Marks this shape: a shared-repository skill.")
    repo_path: RepoPath = Field(description="The skill's path inside the shared skill repository (what follows `git://` on the skill row).")


#: One skill of the bot: exactly one of the three shapes, chosen by `origin` (README §4.2).
SkillEntry = Annotated[LocalSkill | CenterSkill | RepoSkill, Field(discriminator="origin")]


class MemorySpec(ContractModel):
    """The bot's curated memory: how apply writes it, and where the stored item list is (README §4.4)."""

    mode: MemoryMode = Field(description="`seed` writes an item only where the engine has none with that key; `merge` overwrites same keys and keeps the rest; `replace` makes the engine's memory exactly the curated items (T3).")
    items_digest: Digest = Field(description="The stored item list (`MemoryItem` records, canonical JSON, sorted by key).")


class MemoryItem(ContractModel):
    """One curated memory item, inside the stored list `MemorySpec.items_digest` points at."""

    key: MemoryKey = Field(description="The item's stable name, unique in the list, e.g. `returns-window`; patch ops address it.")
    text: MemoryText = Field(description="The fact, rule or lesson itself, e.g. `Returns are accepted within 30 days.`")
    tags: list[str] = Field(description="Free-form topic labels, sorted and unique, e.g. `[\"returns\"]`.")
    source: list[EvidenceRef] = Field(description="What the item was learned from, sorted and unique, e.g. `[\"episode:ep_97\"]`.")


class ResourceEntry(ContractModel):
    """One file or directory the bot has in its workspace, keyed by path."""

    path: ResourcePath = Field(description="Workspace-relative path; ends in `/` for a directory, e.g. `data/kb/`.")
    digest: Digest = Field(description="The file's bytes, or acm-tree-v1 of the directory's files.")


class McpConfig(ContractModel):
    """The bot-level override for one MCP server, the Manifest's closed set; a field left out is not overridden."""

    url: McpUrl = optional_member("Absolute http(s) URL with a host and no userinfo, so no credential is ever stored in a revision.")
    headers: dict[HeaderName, HeaderValue] = optional_member("HTTP headers sent to the server, name to value, as written; unique case-insensitively; never credentials.")
    endpoint_env: Literal["PROD", "PRE"] = optional_member("Which of the server's registered endpoints to use.")
    transport_protocol: Literal["SSE", "STREAMABLE_HTTP"] = optional_member("The MCP transport.")


class McpServerEntry(ContractModel):
    """One MCP server enabled on this bot, keyed by server code, with an optional bot-level override."""

    server_code: Annotated[str, StringConstraints(min_length=1)] = Field(description="The server's code in the MCP registry, e.g. `mcp.ant.homistudio.meetmcp`.")
    config: McpConfig = optional_member("The bot-level override; absent means no override (user-level config and Center defaults are read at apply time).")


class ToolsSpec(ContractModel):
    """Tools the bot uses: only MCP servers in genome v1."""

    mcp: list[McpServerEntry] = Field(max_length=50, description="MCP servers, sorted by `server_code`.")


class ScriptEntry(ContractModel):
    """The bot's startup script, by the digest of its body."""

    digest: Digest = Field(description="The script body's bytes, after the `${BOT_*}` placeholder substitution.")


class GenomeSpec(ContractModel):
    """What the bot is: every category of its definition, each a list of key + value entries (contract §4).

    Hashed into the revision id. Every category is present, even when empty;
    an empty list means none, never "leave as it was".
    """

    persona: list[PersonaFile] = Field(max_length=50, description="Identity files, sorted by type.")
    skills: list[SkillEntry] = Field(max_length=50, description="Skills, sorted by name.")
    memory: MemorySpec | None = Field(description="Curated memory, or null when the bot has none.")
    resources: list[ResourceEntry] = Field(max_length=50, description="Workspace files and directories, sorted by path.")
    tools: ToolsSpec = Field(description="The MCP servers.")
    engine_config: dict[str, Any] = Field(max_length=0, description="Always `{}` in genome v1.")
    script: ScriptEntry | None = Field(description="The startup script, or null when the bot has none.")


class GenomePolicy(ContractModel):
    """What automated actors may change on this bot (contract §5); hashed into the revision id, outside any patch's reach."""

    mutable_genes: list[GenePath] = Field(description="Sorted, unique gene paths patches may change; everything else is off limits.")
    pins: list[GenePath] = Field(description="Sorted, unique gene paths no patch may touch, even inside a mutable gene, e.g. `persona/SAFETY.md`.")
    risk_overrides: dict[GenePath, RiskTier] = Field(description="Gene path → tier for ops overlapping it; can only raise an op's tier. `{}` for none.")
