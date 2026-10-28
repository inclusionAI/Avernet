"""Capture: a bot's current state as a spec (README §7, "capturing the bot's current state").

``capture_spec`` reads a bot through a narrow :class:`BotStateReader` and
applies the contract's normalisation: the platform-written reference block
is removed from ``AGENTS.md`` and the file becomes ``rstrip() + "\\n"``;
local skill files are what ``SkillPackageValidator`` keeps; resource paths
lose empty and ``.`` segments; MCP entries carry only an explicit bot
override. What cannot be recorded is refused by name
(:class:`CaptureRefused`): a resource path the Manifest path rule rejects,
a bot the reader cannot capture, a local skill that is not a valid package.
"""

from __future__ import annotations

import re
from abc import abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, NamedTuple, Protocol, TypeVar

from pydantic import ValidationError

from agentclaw.community.core.content_store import canonical_tree_bytes
from agentclaw.community.core.bot_genome.canonical import sha256_digest
from agentclaw.community.core.bot_genome.errors import CaptureRefused, Reason
from agentclaw.community.core.bot_genome.models import BotRef, ProducedBlob
from agentclaw.community.core.bot_genome.spec_models import (
    CenterSkill,
    ContractModel,
    GenomeSpec,
    LocalSkill,
    McpConfig,
    McpServerEntry,
    PersonaFile,
    RepoSkill,
    ResourceEntry,
    ScriptEntry,
    SkillEntry,
    ToolsSpec,
)
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator

__all__ = [
    "BotStateReader",
    "CapturedFile",
    "CapturedMcpServer",
    "CapturedPersonaFile",
    "CapturedResource",
    "CapturedSkill",
    "CapturedSpec",
    "capture_spec",
    "normalise_resource_path",
]

#: The Manifest's resource path rule (``_PATH_CHARS_RE`` without the placeholder characters).
_PATH_CHARS = re.compile(r"^[A-Za-z0-9._/-]+$")
#: Identity files the engine writes for itself; never persona (README §4.2).
_RESERVED_PERSONA = frozenset({"MEMORY.md", "IDENTITY.md"})


class CapturedFile(NamedTuple):
    """One file as read from the bot: its path and its bytes.

    ``path`` is relative to what holds it: inside a skill it is the path from
    the skill's root (``SKILL.md``, ``scripts/run.py``); for a resource it is
    the file's own name. Example: ``CapturedFile("SKILL.md", b"---\\nname: tidy\\n---\\n")``.
    """

    path: str
    data: bytes


class SkillOrigin(StrEnum):
    """Where a skill's files come from: the prefix of the skill row's locator, and the contract's ``origin``."""

    LOCAL = "local"  #: a bot-owned upload (``local://…``): the files are the bot's, stored as a tree
    CENTER = "center"  #: a Skill Center skill (``center://…``): pinned by version, not copied
    REPO = "repo"  #: the shared skills repository (``git://…``): by repository path, neither copied nor pinned


class CapturedPersonaFile(NamedTuple):
    """One identity file as read from the bot: its file name and its text.

    Example: ``CapturedPersonaFile("SOUL.md", "# Soul\\n…")``. The name is
    the contract's persona ``type``; the text is the file as the engine
    sees it, before capture's normalisation (README §7).
    """

    type: str
    text: str


@dataclass(frozen=True)
class CapturedMcpServer:
    """One MCP server enabled on the bot, with its bot-level override if it has one.

    ``override`` is the raw override as the MCP configuration service holds it, in
    the Manifest's shape (the members of ``McpConfig``: ``url``, ``headers``,
    ``endpoint_env``, ``transport_protocol``); capture validates it into an
    ``McpConfig`` and refuses what the contract cannot record. Example:
    ``CapturedMcpServer("mcp.ant.weather", {"endpoint_env": "PRE"})``; a server
    without an override is ``CapturedMcpServer("mcp.ant.weather", None)``.
    """

    server_code: str  #: the server's code in the MCP registry, the entry's key (``tools/mcp/<code>``)
    override: dict[str, Any] | None = None  #: the bot-level override, or ``None`` when the bot has none


@dataclass(frozen=True)
class CapturedSkill:
    """One active skill as the reader found it, before contract normalisation.

    Which members are set follows ``origin``:

    - ``SkillOrigin.LOCAL`` (a bot-owned upload): ``files`` holds the package's files, e.g.
      ``CapturedSkill("tidy", SkillOrigin.LOCAL, files=(CapturedFile("SKILL.md", …), CapturedFile("a.py", …)))``;
      capture keeps what ``SkillPackageValidator`` keeps and stores them as one acm-tree-v1 tree.
    - ``SkillOrigin.CENTER`` (a Skill Center skill): ``center_version`` is the pinned version,
      e.g. ``CapturedSkill("refund-policy", SkillOrigin.CENTER, center_version="v7")``.
    - ``SkillOrigin.REPO`` (the shared skills repository): ``repo_path`` is the path after ``git://`` on
      the skill row, e.g. ``CapturedSkill("weather", SkillOrigin.REPO, repo_path="team/weather")``.
    """

    name: str  #: the skill's name, which becomes the entry's key (``skills/<name>``)
    origin: SkillOrigin  #: where the files come from (the prefix of the skill row's locator)
    center_version: str | None = None  #: ``center`` only: the Skill Center version in use
    repo_path: str | None = None  #: ``repo`` only: the skill's path inside the shared repository
    files: tuple[CapturedFile, ...] = ()  #: ``local`` only: the package's files, path relative to the skill root


@dataclass(frozen=True)
class CapturedResource:
    """One workspace resource as the reader found it.

    A file resource has exactly one member, e.g.
    ``CapturedResource("data/faq.csv", (CapturedFile("faq.csv", b"q,a\\n"),))``;
    a directory resource's ``path`` ends in ``/`` and ``files`` are its
    members with paths relative to it, e.g.
    ``CapturedResource("data/kb/", (CapturedFile("a.md", b"a"), CapturedFile("b.md", b"b")))``.
    """

    path: str  #: workspace-relative, as the bot's rows name it (``./`` and empty segments are normalised away)
    files: tuple[CapturedFile, ...]  #: the file itself, or a directory's members


class BotStateReader(Protocol):
    """What capture reads a bot through. One production implementation (``bot_state.py``), fakes in tests."""

    @abstractmethod
    async def refusal(self, bot: BotRef) -> str | None:
        """Why this bot cannot be captured at all, or ``None``."""
        ...

    @abstractmethod
    async def persona_files(self, bot: BotRef) -> tuple[CapturedPersonaFile, ...]:
        """Every identity file the bot has, one :class:`CapturedPersonaFile` each, in any order.

        Example: ``(CapturedPersonaFile("SOUL.md", "# Soul\\n…"), CapturedPersonaFile("AGENTS.md", "# Agents\\n…"))``.
        Files the engine writes for itself (``MEMORY.md``, ``IDENTITY.md``) may be included; capture drops them.
        """
        ...

    @abstractmethod
    async def skills(self, bot: BotRef) -> tuple[CapturedSkill, ...]:
        """Every skill active on the bot, one :class:`CapturedSkill` each, in any order."""
        ...

    @abstractmethod
    async def resources(self, bot: BotRef) -> tuple[CapturedResource, ...]:
        """Every workspace resource of the bot, one :class:`CapturedResource` each, in any order."""
        ...

    @abstractmethod
    async def mcp_servers(self, bot: BotRef) -> tuple[CapturedMcpServer, ...]:
        """Every MCP server enabled on the bot, platform defaults excluded, one :class:`CapturedMcpServer` each."""
        ...

    @abstractmethod
    async def script_body(self, bot: BotRef) -> str | None:
        """The startup script body after the ``${BOT_*}`` substitution apply performs, or ``None``."""
        ...


@dataclass(frozen=True)
class CapturedSpec:
    """What capture produced: the spec, and the bytes it names that are not in the store yet."""

    spec: GenomeSpec
    writes: tuple[ProducedBlob, ...]  #: every blob the spec's digests name, for the registry to file


def normalise_resource_path(path: str) -> str:
    """Drop empty and ``.`` segments, keep a trailing ``/``; raises ``ValueError`` when nothing is left or the rule
    fails.
    """
    is_dir = path.endswith("/")
    segments = [segment for segment in path.split("/") if segment not in ("", ".")]
    if not segments:
        raise ValueError("names the workspace root")
    if ".." in segments:
        raise ValueError("has a '..' segment")
    normalised = "/".join(segments) + ("/" if is_dir else "")
    if not _PATH_CHARS.match(normalised):
        raise ValueError("has a character outside A-Z a-z 0-9 . _ - /, which no compiled document can deliver")
    return normalised


def agents_md_text(text: str) -> str:
    """README §7: the platform's reference block removed, then ``rstrip() + "\\n"``."""
    from agentclaw.community.core.services.identity import IdentityService  # lazy: reaches the device dispatcher

    return IdentityService._remove_reference_section(text).rstrip() + "\n"


_M = TypeVar("_M", bound=ContractModel)


def _schema_reasons(location: str, exc: ValidationError) -> list[Reason]:
    """One ``schema`` reason per violation, located at the gene (``persona/SOUL.md``) and the member."""
    return [Reason("schema", location + "".join(f".{p}" for p in error["loc"]), error["msg"]) for error in exc.errors()]


class _Builder:
    """Assembles typed entries, collecting every contract violation instead of stopping at the first."""

    def __init__(self) -> None:
        self.reasons: list[Reason] = []
        self.writes: dict[str, bytes] = {}

    def store(self, data: bytes) -> str:
        digest = sha256_digest(data)
        self.writes[digest] = data
        return digest

    def entry(self, model: type[_M], location: str, **members: Any) -> _M | None:
        """``model(**members)``, or ``None`` with the violations recorded under ``location``.

        ``_M`` is a type variable bounded by ``ContractModel``: ``model`` is
        the class itself (``type[_M]``, e.g. ``PersonaFile``), and the return
        is an instance of that same class, so ``build.entry(PersonaFile, …)``
        is typed as ``PersonaFile | None`` rather than ``ContractModel | None``.
        """
        try:
            return model(**members)
        except ValidationError as exc:  # the bot holds something the contract cannot record
            self.reasons.extend(_schema_reasons(location, exc))
            return None


async def capture_spec(
    reader: BotStateReader, bot: BotRef, *, validator: SkillPackageValidator, bounded: bool = True
) -> CapturedSpec:
    """The bot's current state as a spec, plus the produced bytes to file.

    ``bounded`` applies the per-category limit a revision may hold
    (``GenomeSpec``'s list lengths); a capture that will be recorded keeps
    it. An observation that is only compared, never recorded (the drift
    check), passes ``False``: an applied directory resource is one revision
    entry but one row per member file on the bot, so the bot can hold more
    file entries than a revision may, and still be exactly its revision.
    Every entry is validated either way; only the list-level limit is skipped.
    """
    refusal = await reader.refusal(bot)
    if refusal is not None:
        raise CaptureRefused([Reason("not_capturable", "bot", refusal)])
    build = _Builder()

    persona: list[PersonaFile] = []
    for file in await reader.persona_files(bot):
        if file.type in _RESERVED_PERSONA:
            continue
        text = agents_md_text(file.text) if file.type == "AGENTS.md" else file.text
        entry = build.entry(
            PersonaFile, f"persona/{file.type}", type=file.type, digest=build.store(text.encode("utf-8"))
        )
        if entry is not None:
            persona.append(entry)

    skills: list[SkillEntry] = []
    for skill in await reader.skills(bot):
        location = f"skills/{skill.name}"
        entry: SkillEntry | None
        if skill.origin == SkillOrigin.LOCAL:
            try:
                validated = validator.validate_directory(list(skill.files))
            except ValueError as exc:
                build.reasons.append(Reason("skill_package", location, f"not a valid skill package: {exc}"))
                continue
            digest = build.store(canonical_tree_bytes(list(validated.files)))
            entry = build.entry(LocalSkill, location, name=skill.name, origin="local", digest=digest)
        elif skill.origin == SkillOrigin.CENTER:
            if not skill.center_version:
                build.reasons.append(
                    Reason(
                        "skill_package", location, "a Skill Center skill without a pinned version cannot be recorded"
                    )
                )
                continue
            entry = build.entry(
                CenterSkill, location, name=skill.name, origin="center", center_version=skill.center_version
            )
        elif skill.origin == SkillOrigin.REPO:
            if not skill.repo_path:
                build.reasons.append(
                    Reason(
                        "skill_package",
                        location,
                        "a shared-repository skill without a repository path cannot be recorded",
                    )
                )
                continue
            entry = build.entry(RepoSkill, location, name=skill.name, origin="repo", repo_path=skill.repo_path)
        else:
            build.reasons.append(Reason("skill_package", location, f"unknown skill origin {skill.origin!r}"))
            continue
        if entry is not None:
            skills.append(entry)

    resources: list[ResourceEntry] = []
    seen: dict[str, str] = {}
    for resource in await reader.resources(bot):
        location = f"resources/{resource.path}"
        try:
            path = normalise_resource_path(resource.path)
        except ValueError as exc:
            build.reasons.append(Reason("resource_path", location, f"{resource.path!r} {exc}"))
            continue
        if path.rstrip("/") in seen:
            build.reasons.append(
                Reason(
                    "resource_path",
                    location,
                    f"{resource.path!r} and {seen[path.rstrip('/')]!r} name one workspace path",
                )
            )
            continue
        seen[path.rstrip("/")] = resource.path
        if path.endswith("/"):
            digest = build.store(canonical_tree_bytes(list(resource.files)))
        elif len(resource.files) == 1:
            digest = build.store(resource.files[0].data)
        else:
            build.reasons.append(Reason("resource_path", location, "a file resource has exactly one member"))
            continue
        entry = build.entry(ResourceEntry, f"resources/{path}", path=path, digest=digest)
        if entry is not None:
            resources.append(entry)

    mcp: list[McpServerEntry] = []
    for server in await reader.mcp_servers(bot):
        code = server.server_code
        members: dict[str, Any] = {"server_code": code}
        if server.override:  # an explicit override only; the member is left out otherwise, never null
            members["config"] = build.entry(McpConfig, f"tools/mcp/{code}.config", **server.override)
            if members["config"] is None:
                continue
        entry = build.entry(McpServerEntry, f"tools/mcp/{code}", **members)
        if entry is not None:
            mcp.append(entry)

    body = await reader.script_body(bot)
    script = build.entry(ScriptEntry, "script", digest=build.store(body.encode("utf-8"))) if body else None
    if build.reasons:
        raise CaptureRefused(build.reasons)

    members: dict[str, Any] = dict(
        persona=sorted(persona, key=lambda e: e.type),
        skills=sorted(skills, key=lambda e: e.name),
        memory=None,
        resources=sorted(resources, key=lambda e: e.path),
        tools=ToolsSpec(mcp=sorted(mcp, key=lambda e: e.server_code)),
        engine_config={},
        script=script,
    )
    if not bounded:
        return CapturedSpec(
            spec=GenomeSpec.model_construct(**members),
            writes=tuple(ProducedBlob(digest, data) for digest, data in build.writes.items()),
        )
    try:
        spec = GenomeSpec(**members)
    except (
        ValidationError
    ) as exc:  # a list-level rule the entries cannot carry, e.g. more entries than a category holds
        raise CaptureRefused(_schema_reasons("spec", exc)) from exc
    return CapturedSpec(spec=spec, writes=tuple(ProducedBlob(digest, data) for digest, data in build.writes.items()))
