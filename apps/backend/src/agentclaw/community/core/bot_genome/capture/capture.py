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
from typing import Any, Protocol

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import canonical_tree_bytes
from agentclaw.community.core.bot_genome.canonical import sha256_digest
from agentclaw.community.core.bot_genome.errors import CaptureRefused, Reason
from agentclaw.community.core.bot_genome.models import BotRef
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator

__all__ = [
    "BotStateReader",
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


@dataclass(frozen=True)
class CapturedSkill:
    """One active skill as the reader found it; ``files`` only for ``local``."""

    name: str
    origin: str  # local | center | repo
    center_version: str | None = None
    repo_path: str | None = None
    files: tuple[tuple[str, bytes], ...] = ()


@dataclass(frozen=True)
class CapturedResource:
    """One workspace resource; ``path`` ends in ``/`` for a directory, whose ``files`` are its members."""

    path: str
    files: tuple[tuple[str, bytes], ...]


class BotStateReader(Protocol):
    """What capture reads a bot through. One production implementation (``bot_state.py``), fakes in tests."""

    @abstractmethod
    async def refusal(self, bot: BotRef) -> str | None:
        """Why this bot cannot be captured at all, or ``None``."""
        ...

    @abstractmethod
    async def persona_files(self, bot: BotRef) -> dict[str, str]:
        """``type → text`` of every identity file the bot has."""
        ...

    @abstractmethod
    async def skills(self, bot: BotRef) -> tuple[CapturedSkill, ...]: ...

    @abstractmethod
    async def resources(self, bot: BotRef) -> tuple[CapturedResource, ...]: ...

    @abstractmethod
    async def mcp_servers(self, bot: BotRef) -> tuple[dict[str, Any], ...]:
        """``{"server_code": …, "config": …?}`` per enabled server, platform defaults excluded."""
        ...

    @abstractmethod
    async def script_body(self, bot: BotRef) -> str | None:
        """The startup script body after the ``${BOT_*}`` substitution apply performs, or ``None``."""
        ...


@dataclass(frozen=True)
class CapturedSpec:
    spec: dict[str, Any]
    writes: tuple[tuple[str, bytes], ...]


def normalise_resource_path(path: str) -> str:
    """Drop empty and ``.`` segments, keep a trailing ``/``; raises ``ValueError`` when nothing is left or the rule fails."""
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


async def capture_spec(reader: BotStateReader, bot: BotRef, *, validator: SkillPackageValidator) -> CapturedSpec:
    """The bot's current state as a spec, plus the produced bytes to file."""
    refusal = await reader.refusal(bot)
    if refusal is not None:
        raise CaptureRefused([Reason("not_capturable", "bot", refusal)])
    writes: dict[str, bytes] = {}

    def store(data: bytes) -> str:
        digest = sha256_digest(data)
        writes[digest] = data
        return digest

    persona = []
    for file_type, text in (await reader.persona_files(bot)).items():
        if file_type in _RESERVED_PERSONA:
            continue
        if file_type == "AGENTS.md":
            text = agents_md_text(text)
        persona.append({"type": file_type, "digest": store(text.encode("utf-8"))})

    skills = []
    reasons: list[Reason] = []
    for skill in await reader.skills(bot):
        location = f"skills/{skill.name}"
        if skill.origin == "local":
            try:
                validated = validator.validate_directory(list(skill.files))
            except ValueError as exc:
                reasons.append(Reason("skill_package", location, f"not a valid skill package: {exc}"))
                continue
            skills.append({"name": skill.name, "origin": "local", "digest": store(canonical_tree_bytes(list(validated.files)))})
        elif skill.origin == "center":
            if not skill.center_version:
                reasons.append(Reason("skill_package", location, "a Skill Center skill without a pinned version cannot be recorded"))
                continue
            skills.append({"name": skill.name, "origin": "center", "center_version": skill.center_version})
        elif skill.origin == "repo":
            if not skill.repo_path:
                reasons.append(Reason("skill_package", location, "a shared-repository skill without a repository path cannot be recorded"))
                continue
            skills.append({"name": skill.name, "origin": "repo", "repo_path": skill.repo_path})
        else:
            reasons.append(Reason("skill_package", location, f"unknown skill origin {skill.origin!r}"))

    resources = []
    seen: dict[str, str] = {}
    for resource in await reader.resources(bot):
        location = f"resources/{resource.path}"
        try:
            path = normalise_resource_path(resource.path)
        except ValueError as exc:
            reasons.append(Reason("resource_path", location, f"{resource.path!r} {exc}"))
            continue
        if path.rstrip("/") in seen:
            reasons.append(Reason("resource_path", location, f"{resource.path!r} and {seen[path.rstrip('/')]!r} name one workspace path"))
            continue
        seen[path.rstrip("/")] = resource.path
        if path.endswith("/"):
            digest = store(canonical_tree_bytes(list(resource.files)))
        elif len(resource.files) == 1:
            digest = store(resource.files[0][1])
        else:
            reasons.append(Reason("resource_path", location, "a file resource has exactly one member"))
            continue
        resources.append({"path": path, "digest": digest})
    if reasons:
        raise CaptureRefused(reasons)

    mcp = []
    for server in await reader.mcp_servers(bot):
        entry: dict[str, Any] = {"server_code": server["server_code"]}
        if server.get("config"):
            entry["config"] = dict(server["config"])
        mcp.append(entry)

    body = await reader.script_body(bot)
    script = {"digest": store(body.encode("utf-8"))} if body else None

    spec = {
        "persona": sorted(persona, key=lambda e: e["type"]),
        "skills": sorted(skills, key=lambda e: e["name"]),
        "memory": None,
        "resources": sorted(resources, key=lambda e: e["path"]),
        "tools": {"mcp": sorted(mcp, key=lambda e: e["server_code"])},
        "engine_config": {},
        "script": script,
    }
    return CapturedSpec(spec=spec, writes=tuple(writes.items()))
