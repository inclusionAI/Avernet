"""Everything apply would refuse is refused when a revision is recorded (README §6.4, §3.1, §3.4, §4.2).

The predicates here run on a spec and the bytes its digests name, with the
same rules the Manifest materialisers run before writing: persona UTF-8 and
engine-legal types, local skills as ``SkillPackageValidator`` packages,
resource admission and path rules, the MCP URL and header rules, and the
MCP permission and bot-override checks in the recording bot's context. Each
predicate returns the :class:`Reason` list it found; ``SpecChecks.reasons``
runs them all so a refusal lists everything at once.

Codes: ``schema``, ``order``, ``not_canonical``, ``not_utf8``,
``persona_type``, ``skill_package``, ``tree``, ``admission``,
``resource_paths``, ``mcp_entry``, ``mcp_not_permitted``, ``mcp_override``,
``limit_exceeded``, ``content_not_reachable``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from agentclaw.community.core.bot_config_manifest.apply.entry_delivery import (
    canonical_tree_bytes,
    decode_tree_bytes,
)
from agentclaw.community.core.bot_config_manifest.capabilities import ManifestCapabilities
from agentclaw.community.core.bot_config_manifest.schema._support import Context
from agentclaw.community.core.bot_config_manifest.schema.entries import (
    legal_identity_types,
    validate_mcp_entry,
)
from agentclaw.community.core.bot_genome import contract
from agentclaw.community.core.bot_genome.canonical import (
    NotCanonicalizable,
    is_canonical_gene_path,
    loads_strict,
)
from agentclaw.community.core.bot_genome.errors import ContentNotReachable, Reason
from agentclaw.community.core.bot_genome.limits import (
    MAX_PERSONA_BYTES,
    MAX_RESOURCE_FILE_BYTES,
    MAX_SEGMENT_BYTES,
    MAX_SKILL_BYTES,
    MAX_TREE_BYTES,
    MAX_TREE_MEMBERS,
    MAX_WRITTEN_PATH_BYTES,
)
from agentclaw.community.core.bot_genome.models import ContentReader
from agentclaw.community.core.bot_genome.spec_models import GenomePolicy, GenomeSpec, LocalSkill
from agentclaw.community.core.mcp.mcp_auth_service_protocol import MCPAuthServiceProtocol
from agentclaw.community.core.mcp.mcp_config_service_protocol import MCPConfigServiceProtocol
from agentclaw.community.core.resources.services.file_service import admission_refusal
from agentclaw.community.core.skill_center.skill_package import SkillPackageValidator

__all__ = [
    "SpecCheckContext",
    "SpecChecks",
    "mcp_entry_reasons",
    "order_reasons",
    "persona_reasons",
    "policy_path_reasons",
    "resource_reasons",
    "schema_reasons",
    "skill_reasons",
    "tree_reasons",
]

_SORTED = (("persona", "type"), ("skills", "name"), ("resources", "path"))
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class SpecCheckContext:
    """The recording bot's context: what the MCP and persona predicates read outside the revision."""

    engine_type: str
    bot_type: str
    owner_id: str
    actor_id: str


# ---------------------------------------------------------------------------------------------
# Shape


def schema_reasons(spec: GenomeSpec, policy: GenomePolicy) -> list[Reason]:
    """``spec`` and ``policy`` against their contract schema definitions (code ``schema``).

    The check runs on the JSON form (``to_json()``): the contract schema is
    the oracle for the patterns and for the rules a type cannot carry, so a
    record built in code (``model_construct``) is held to it too.
    """
    reasons = []
    for name, value in (("spec", spec.to_json()), ("policy", policy.to_json())):
        validator = contract.definition_validator(contract.REVISION_SCHEMA_FILE, name)
        for error in sorted(validator.iter_errors(value), key=lambda e: list(e.absolute_path)):
            path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in error.absolute_path)
            reasons.append(Reason("schema", name + path, error.message))
    return reasons


def order_reasons(spec: GenomeSpec, policy: GenomePolicy) -> list[Reason]:
    """README §3.1: every list sorted in code-point order and unique by its key (code ``order``)."""
    reasons = []
    lists: list[tuple[str, list[str]]] = [
        (f"spec.{category}", [getattr(entry, key) for entry in getattr(spec, category)]) for category, key in _SORTED
    ]
    lists.append(("spec.tools.mcp", [entry.server_code for entry in spec.tools.mcp]))
    lists.append(("policy.mutable_genes", list(policy.mutable_genes)))
    lists.append(("policy.pins", list(policy.pins)))
    for location, keys in lists:
        if len(keys) != len(set(keys)):
            reasons.append(Reason("order", location, "keys must be unique"))
        elif keys != sorted(keys):
            reasons.append(Reason("order", location, "entries must be sorted by key in code-point order"))
    return reasons


def policy_path_reasons(policy: GenomePolicy) -> list[Reason]:
    """README §2: every gene path in the policy in its canonical spelling (code ``not_canonical``)."""
    reasons = []
    paths = [("policy.mutable_genes", p) for p in policy.mutable_genes]
    paths += [("policy.pins", p) for p in policy.pins]
    paths += [("policy.risk_overrides", p) for p in policy.risk_overrides]
    for location, path in paths:
        if not is_canonical_gene_path(path):
            reasons.append(Reason("not_canonical", location, f"{path!r} is not in its canonical spelling"))
    return reasons


# ---------------------------------------------------------------------------------------------
# Trees (README §3.4)


def _segments_ok(path: str) -> str | None:
    """Why a tree member or resource path cannot be written, or ``None``."""
    if path.startswith("/") or "\\" in path or path.endswith("/") or _DRIVE_LETTER.match(path):
        return "a path is relative, '/'-separated and names a file"
    if any(ord(c) < 32 or c == "\x7f" for c in path):
        return "a path must not contain a control character"
    for segment in path.split("/"):
        if segment in ("", ".", ".."):
            return "a path has no empty, '.' or '..' segment"
        if len(segment.encode("utf-8")) > MAX_SEGMENT_BYTES:
            return f"a path segment is at most {MAX_SEGMENT_BYTES} bytes of UTF-8"
    return None


def _prefix_collision(paths: Sequence[str]) -> tuple[str, str] | None:
    """A path that is a whole-segment prefix of another, or ``None``."""
    ordered = sorted(set(paths))
    for index, shorter in enumerate(ordered):
        for longer in ordered[index + 1 :]:
            if not longer.startswith(shorter):
                break
            if longer.startswith(shorter + "/"):
                return shorter, longer
    return None


def tree_reasons(location: str, blob: bytes) -> tuple[list[Reason], list[tuple[str, bytes]]]:
    """README §3.4: canonical encoding, unique writable member paths, no member beneath another."""
    members = decode_tree_bytes(blob)
    if members is None:
        return [Reason("tree", location, "not an acm-tree-v1 blob")], []
    paths = [path for path, _ in members]
    if len(paths) != len(set(paths)):
        return [Reason("tree", location, "a tree lists one path twice")], members
    if canonical_tree_bytes(members) != blob:
        return [Reason("tree", location, "the tree is not in canonical member order")], members
    for path in paths:
        problem = _segments_ok(path)
        if problem is not None:
            return [Reason("tree", f"{location}:{path!r}", problem)], members
    collision = _prefix_collision(paths)
    if collision is not None:
        return [Reason("tree", location, f"{collision[0]!r} is also the directory of {collision[1]!r}")], members
    if len(members) > MAX_TREE_MEMBERS:
        return [Reason("limit_exceeded", location, f"a tree holds at most {MAX_TREE_MEMBERS} files")], members
    return [], members


# ---------------------------------------------------------------------------------------------
# Categories


def persona_reasons(spec: GenomeSpec, content: ContentReader, engine_type: str) -> list[Reason]:
    """UTF-8 bytes within the identity cap and a type the bot's engine accepts (``not_utf8``, ``limit_exceeded``,
    ``persona_type``).
    """
    reasons = []
    legal = legal_identity_types(engine_type)
    for index, entry in enumerate(spec.persona):
        location = f"spec.persona[{index}]"
        if entry.type not in legal:
            reasons.append(Reason("persona_type", location, f"engine {engine_type!r} does not accept {entry.type}"))
        data = _read(content, entry.digest, location, reasons)
        if data is None:
            continue
        if len(data) > MAX_PERSONA_BYTES:
            reasons.append(Reason("limit_exceeded", location, f"a persona file is at most {MAX_PERSONA_BYTES} bytes"))
        elif not _is_utf8(data):
            reasons.append(Reason("not_utf8", location, f"{entry.type} is not UTF-8 text"))
    return reasons


def skill_reasons(spec: GenomeSpec, content: ContentReader, validator: SkillPackageValidator) -> list[Reason]:
    """A local skill's tree is a valid package whose metadata name equals the entry name (README §4.2)."""
    reasons = []
    for index, entry in enumerate(spec.skills):
        if not isinstance(entry, LocalSkill):
            continue
        location = f"spec.skills[{index}]"
        blob = _read(content, entry.digest, location, reasons)
        if blob is None:
            continue
        if len(blob) > MAX_SKILL_BYTES:
            reasons.append(Reason("limit_exceeded", location, f"a skill is at most {MAX_SKILL_BYTES} bytes"))
            continue
        found, members = tree_reasons(location, blob)
        if found:
            reasons.extend(found)
            continue
        try:
            validated = validator.validate_directory(members)
        except ValueError as exc:  # SkillPackageInvalidError, SkillPackageTooLargeError and their kin
            reasons.append(Reason("skill_package", location, f"not a valid skill package: {exc}"))
            continue
        if validated.name != entry.name:
            reasons.append(
                Reason("skill_package", location, f"SKILL.md names {validated.name!r}, the entry {entry.name!r}")
            )
        elif sorted(validated.files) != sorted(members):
            reasons.append(
                Reason("skill_package", location, "the tree is not what the package validator keeps (README §4.2)")
            )
    return reasons


def resource_reasons(spec: GenomeSpec, content: ContentReader) -> list[Reason]:
    """Admission of every written file and the path rules of README §4.2 (codes ``admission``, ``resource_paths``)."""
    reasons = []
    stripped = [entry.path.rstrip("/") for entry in spec.resources]
    if len(stripped) != len(set(stripped)):
        dup = next(p for p in stripped if stripped.count(p) > 1)
        reasons.append(Reason("resource_paths", "spec.resources", f"{dup!r} and {dup + '/'!r} name one workspace path"))
    collision = _prefix_collision(stripped)
    if collision is not None:
        reasons.append(Reason("resource_paths", "spec.resources", f"{collision[1]!r} lies beneath {collision[0]!r}"))
    for index, entry in enumerate(spec.resources):
        location = f"spec.resources[{index}]"
        path = entry.path
        problem = _segments_ok(path.rstrip("/"))
        if problem is not None:
            reasons.append(Reason("resource_paths", location, problem))
            continue
        blob = _read(content, entry.digest, location, reasons)
        if blob is None:
            continue
        if path.endswith("/"):
            if len(blob) > MAX_TREE_BYTES:
                reasons.append(
                    Reason("limit_exceeded", location, f"a directory resource is at most {MAX_TREE_BYTES} bytes")
                )
                continue
            found, members = tree_reasons(location, blob)
            reasons.extend(found)
            if found:
                continue
            files = [(path + member, data) for member, data in members]
        else:
            if len(blob) > MAX_RESOURCE_FILE_BYTES:
                reasons.append(
                    Reason("limit_exceeded", location, f"a file resource is at most {MAX_RESOURCE_FILE_BYTES} bytes")
                )
                continue
            files = [(path, blob)]
        for written, data in files:
            if len(written.encode("utf-8")) > MAX_WRITTEN_PATH_BYTES:
                reasons.append(
                    Reason(
                        "resource_paths",
                        f"{location}:{written!r}",
                        f"a written path is at most {MAX_WRITTEN_PATH_BYTES} bytes",
                    )
                )
                continue
            refusal = admission_refusal(written.rsplit("/", 1)[-1], data)
            if refusal is not None:
                reasons.append(Reason("admission", f"{location}:{written!r}", refusal))
    return reasons


def mcp_entry_reasons(spec: GenomeSpec, ctx: SpecCheckContext) -> list[Reason]:
    """The Manifest's own MCP entry rules (URL with a host, header names and values), plus no userinfo."""
    manifest_ctx = Context(capabilities=ManifestCapabilities(ctx.engine_type, ctx.bot_type, (), ()))
    reasons = []
    for index, entry in enumerate(spec.tools.mcp):
        location = f"spec.tools.mcp[{index}]"
        validate_mcp_entry(manifest_ctx, location, entry.to_json())  # its own copy: the Manifest rule may normalise it
        url = entry.config.url if entry.config is not None else None
        if isinstance(url, str):
            try:
                parsed = urlsplit(url)
            except ValueError:
                parsed = None
            if parsed is not None and (parsed.username is not None or parsed.password is not None):
                reasons.append(Reason("mcp_entry", f"{location}.config.url", "an MCP url must not carry userinfo"))
    reasons.extend(Reason("mcp_entry", v.location, v.message) for v in manifest_ctx.violations)
    return reasons


def memory_reasons(spec: GenomeSpec, content: ContentReader) -> list[Reason]:
    """The stored item list: canonical JSON of ``memoryItems``, sorted by key, keys unique."""
    memory = spec.memory
    if memory is None:
        return []
    reasons: list[Reason] = []
    blob = _read(content, memory.items_digest, "spec.memory.items_digest", reasons)
    if blob is None:
        return reasons
    try:
        items = loads_strict(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, NotCanonicalizable):
        return [Reason("schema", "spec.memory.items_digest", "the stored items are not canonical JSON")]
    validator = contract.definition_validator(contract.REVISION_SCHEMA_FILE, "memoryItems")
    for error in validator.iter_errors(items):
        reasons.append(Reason("schema", "spec.memory.items_digest", error.message))
    if not reasons:
        keys = [item["key"] for item in items]
        if len(keys) != len(set(keys)) or keys != sorted(keys):
            reasons.append(Reason("order", "spec.memory.items_digest", "items must be sorted by key and unique"))
        for item in items:
            for field in ("tags", "source"):
                if item[field] != sorted(item[field]):
                    reasons.append(
                        Reason("order", f"spec.memory.items_digest:{item['key']}.{field}", f"{field} must be sorted")
                    )
    return reasons


def script_reasons(spec: GenomeSpec, content: ContentReader) -> list[Reason]:
    reasons: list[Reason] = []
    if spec.script is not None:
        data = _read(content, spec.script.digest, "spec.script", reasons)
        if data is not None and not _is_utf8(data):
            reasons.append(Reason("not_utf8", "spec.script", "the script body is not UTF-8 text"))
    return reasons


# ---------------------------------------------------------------------------------------------
# The context checks and the bundle


class SpecChecks:
    """Every README §6.4 predicate over a spec, bound to the services the context checks need.

    ``mcp_auth`` and ``mcp_config`` are the same services the MCP
    materialiser asks; a failure to reach them reads as "not permitted",
    exactly as apply reads it.
    """

    def __init__(
        self,
        *,
        validator: SkillPackageValidator,
        mcp_auth: MCPAuthServiceProtocol,
        mcp_config: MCPConfigServiceProtocol,
    ) -> None:
        self._validator = validator
        self._mcp_auth = mcp_auth
        self._mcp_config = mcp_config

    def mcp_context_reasons(self, spec: GenomeSpec, ctx: SpecCheckContext) -> list[Reason]:
        """``McpMaterialiser.resolve``'s two questions: may the actor enable it, and is the override valid."""
        reasons = []
        for index, entry in enumerate(spec.tools.mcp):
            location, code = f"spec.tools.mcp[{index}]", entry.server_code
            try:
                verdict = self._mcp_auth.check_mcp_permission_detail(ctx.actor_id, code)
                permitted = bool(verdict.get("has_permission")) and bool(verdict.get("access_level"))
            except Exception:  # noqa: BLE001 - an unreachable check is not a yes (materialisers/mcp.py)
                permitted = False
            if not permitted:
                reasons.append(Reason("mcp_not_permitted", location, f"{ctx.actor_id!r} may not enable {code!r}"))
                continue
            config = entry.config.to_json() if entry.config is not None else None
            result = self._mcp_config.validate_bot_override(
                user_id=ctx.owner_id, server_code=code, config=config if config else None, engine_type=ctx.engine_type
            )
            if not result.get("valid"):
                reasons.append(Reason("mcp_override", location, str(result.get("error") or "invalid config")))
        return reasons

    def reasons(
        self, spec: GenomeSpec, policy: GenomePolicy, content: ContentReader, ctx: SpecCheckContext
    ) -> list[Reason]:
        """Everything, shape first: a record that fails the schema is not walked further."""
        found = schema_reasons(spec, policy)
        if found:
            return found
        found = order_reasons(spec, policy) + policy_path_reasons(policy)
        if found:
            return found
        checks: list[Callable[[], list[Reason]]] = [
            lambda: persona_reasons(spec, content, ctx.engine_type),
            lambda: skill_reasons(spec, content, self._validator),
            lambda: resource_reasons(spec, content),
            lambda: mcp_entry_reasons(spec, ctx),
            lambda: memory_reasons(spec, content),
            lambda: script_reasons(spec, content),
        ]
        for check in checks:
            found.extend(check())
        if not any(r.code == "mcp_entry" for r in found):
            found.extend(self.mcp_context_reasons(spec, ctx))
        return found


# ---------------------------------------------------------------------------------------------


def _is_utf8(data: bytes) -> bool:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _read(content: ContentReader, digest: str, location: str, reasons: list[Reason]) -> bytes | None:
    try:
        return content.read(digest)
    except ContentNotReachable:
        reasons.append(Reason("content_not_reachable", location, f"{digest} is not reachable for this bot"))
        return None
