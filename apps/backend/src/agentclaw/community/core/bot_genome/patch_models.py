"""A Genome Patch as classes: ``GenomePatch`` and its twelve op shapes (contract §6).

Same arrangement as :mod:`spec_models`: the shape is written out, the value
patterns come from the contract schema at import, and the contract examples
pin the two together. ``PatchOp`` is the discriminated union the op's ``op``
member selects; ``TextEdit`` the one ``kind`` selects.
"""
from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Self

from pydantic import AfterValidator, Field, StringConstraints, TypeAdapter, model_validator

from agentclaw.community.core.bot_genome import contract
from agentclaw.community.core.bot_genome.spec_models import (
    ContractModel,
    Digest,
    EvidenceRef,
    GenePath,
    MemoryItem,
    MemoryKey,
    MemoryMode,
    PersonaFileType,
    ResourcePath,
    SkillName,
    constrained,
    optional_member,
    schema_property,
)

__all__ = [
    "Content",
    "DiffEdit",
    "DigestRef",
    "FileDiff",
    "FileEdit",
    "GenomePatch",
    "InsertAfter",
    "MemoryAdd",
    "MemoryRetire",
    "MemorySetMode",
    "MemoryUpdate",
    "PatchOp",
    "PersonaAdd",
    "PersonaRemove",
    "ReplaceSection",
    "ResourceAdd",
    "ResourceRemove",
    "SkillAdd",
    "SkillRemove",
    "SkillUpdate",
    "TextEdit",
    "parse_memory_items",
    "parse_ops",
]

_PATCH = contract.PATCH_SCHEMA_FILE
_TEXT_EDITS = ("definitions", "textEdit", "oneOf")

UnifiedDiff = constrained(_PATCH, "definitions", "unifiedDiff")
SkillFilePath = constrained(_PATCH, "definitions", "skillFilePath")
Heading = constrained(_PATCH, *_TEXT_EDITS, 0, "properties", "heading")
Anchor = constrained(_PATCH, *_TEXT_EDITS, 1, "properties", "anchor")
InlineText = constrained(_PATCH, "definitions", "content", "oneOf", 0)
Rationale = constrained(_PATCH, "properties", "rationale")
MAX_EVIDENCE_REFS: int = schema_property(_PATCH, "properties", "evidence")["maxItems"]
_TARGET_SHAPE = re.compile(schema_property(_PATCH, "definitions", "fileEdit", "properties", "target", "allOf", 1)["pattern"])


def _a_file_gene(target: str) -> str:
    if not _TARGET_SHAPE.match(target):
        raise ValueError("a file.edit target is a persona file, a file inside a skill, or a resource file")
    return target


#: A `file.edit` target: a gene path that names a file.
FileTarget = Annotated[GenePath, AfterValidator(_a_file_gene)]


class DigestRef(ContractModel):
    """Bytes uploaded first with `PUT …/genome/content`, named by digest."""

    digest: Digest = Field(description="The uploaded bytes.")


#: Inline UTF-8 text (the 64 KiB byte limit is checked when the patch is recorded), or uploaded bytes by digest.
Content = InlineText | DigestRef


# ---------------------------------------------------------------------------------------------
# Text edits (README §6.2)


class ReplaceSection(ContractModel):
    """Replace everything under a Markdown heading; the heading line stays."""

    kind: Literal["replace_section"] = Field(description="Marks this edit kind.")
    heading: Heading = Field(description="The heading line, e.g. `## Escalation`; must occur exactly once.")
    content: InlineText = Field(description="The new text under the heading.")


class InsertAfter(ContractModel):
    """Insert text after an anchor line."""

    kind: Literal["insert_after"] = Field(description="Marks this edit kind.")
    anchor: Anchor = Field(description="One whole line of the file, which must occur exactly once.")
    content: Annotated[str, StringConstraints(min_length=1, max_length=65536)] = Field(description="The text to insert.")


class DiffEdit(ContractModel):
    """Apply a unified diff; expresses any text change."""

    kind: Literal["diff"] = Field(description="Marks this edit kind.")
    diff: UnifiedDiff = Field(description="The hunks to apply (`@@ … @@` lines, then ` `, `-`, `+` lines; no `---`/`+++` header).")


#: One edit to a text file: exactly one of the three kinds, chosen by `kind`.
TextEdit = Annotated[ReplaceSection | InsertAfter | DiffEdit, Field(discriminator="kind")]


# ---------------------------------------------------------------------------------------------
# Ops (README §6.1)


class FileEdit(ContractModel):
    """Op `file.edit`: edits one existing text file in place."""

    op: Literal["file.edit"] = Field(description="Marks this op shape.")
    target: FileTarget = Field(description="A persona file, a file inside a skill, a file resource, or a file inside a directory resource, e.g. `persona/SOUL.md`.")
    edits: list[TextEdit] = Field(min_length=1, max_length=100, description="The edits, applied in order to the file.")


class PersonaAdd(ContractModel):
    """Op `persona.add`: adds an identity file the bot does not have yet."""

    op: Literal["persona.add"] = Field(description="Marks this op shape.")
    type: PersonaFileType = Field(description="Which identity file to add.")
    content: Content = Field(description="The file's text, inline or by digest; must be valid UTF-8.")


class PersonaRemove(ContractModel):
    """Op `persona.remove`: removes an identity file."""

    op: Literal["persona.remove"] = Field(description="Marks this op shape.")
    type: PersonaFileType = Field(description="Which identity file to remove.")


class SkillAdd(ContractModel):
    """Op `skill.add`: adds a local skill from its files."""

    op: Literal["skill.add"] = Field(description="Marks this op shape.")
    name: SkillName = Field(description="The new skill's name; equals the SKILL.md metadata name.")
    files: dict[SkillFilePath, Content] = Field(max_length=500, description="Path inside the skill → content (inline text, or `{digest}` for uploaded bytes); must include `SKILL.md`.")

    @model_validator(mode="after")
    def _has_skill_md(self) -> Self:
        if "SKILL.md" not in self.files:
            raise ValueError("a skill's files must include SKILL.md")
        return self


class FileDiff(ContractModel):
    """One file's unified diff inside a `skill.update`."""

    path: SkillFilePath = Field(description="The file to change, e.g. `SKILL.md`.")
    diff: UnifiedDiff = Field(description="The hunks to apply to it.")


class SkillUpdate(ContractModel):
    """Op `skill.update`: changes files of an existing local skill, by diffs, by whole-file content, or both."""

    op: Literal["skill.update"] = Field(description="Marks this op shape.")
    name: SkillName = Field(description="The skill to change.")
    file_diffs: list[FileDiff] = optional_member("Unified diffs to existing text files of the skill; at most one per file.", min_length=1, max_length=500)
    files: dict[SkillFilePath, Content | None] = optional_member("Whole-file writes, path → new content, or null to delete that file; adds a new path, replaces an existing one. `SKILL.md` can be replaced but not deleted.", min_length=1)

    @model_validator(mode="after")
    def _at_least_one_and_skill_md_stays(self) -> Self:
        if self.file_diffs is None and self.files is None:
            raise ValueError("a skill.update carries file_diffs, files, or both")
        if self.files is not None and "SKILL.md" in self.files and self.files["SKILL.md"] is None:
            raise ValueError("SKILL.md cannot be deleted")
        return self


class SkillRemove(ContractModel):
    """Op `skill.remove`: removes a skill, local or center."""

    op: Literal["skill.remove"] = Field(description="Marks this op shape.")
    name: SkillName = Field(description="The skill to remove.")


class ResourceAdd(ContractModel):
    """Op `resource.add`: adds a file resource, or a directory resource from an uploaded acm-tree-v1 tree."""

    op: Literal["resource.add"] = Field(description="Marks this op shape.")
    path: ResourcePath = Field(description="Where the resource goes in the workspace; ends in `/` for a directory.")
    content: Content = Field(description="The file's content, inline or by digest; a directory is always `{digest}` of an uploaded tree.")

    @model_validator(mode="after")
    def _a_directory_comes_from_a_tree(self) -> Self:
        if self.path.endswith("/") and not isinstance(self.content, DigestRef):
            raise ValueError("a directory resource is added from an uploaded acm-tree-v1 tree, by digest")
        return self


class ResourceRemove(ContractModel):
    """Op `resource.remove`: removes a resource entry."""

    op: Literal["resource.remove"] = Field(description="Marks this op shape.")
    path: ResourcePath = Field(description="The entry to remove, as declared.")


class MemoryAdd(ContractModel):
    """Op `memory.add`: adds a curated memory item with a new key."""

    op: Literal["memory.add"] = Field(description="Marks this op shape.")
    item: MemoryItem = Field(description="The new item.")


class MemoryUpdate(ContractModel):
    """Op `memory.update`: replaces the text, tags and source of an existing item, by key."""

    op: Literal["memory.update"] = Field(description="Marks this op shape.")
    item: MemoryItem = Field(description="The item's new value; its key names the item to replace.")


class MemoryRetire(ContractModel):
    """Op `memory.retire`: removes a curated memory item, by key; earlier revisions keep it."""

    op: Literal["memory.retire"] = Field(description="Marks this op shape.")
    key: MemoryKey = Field(description="The item to retire.")


class MemorySetMode(ContractModel):
    """Op `memory.set_mode`: changes how apply writes the curated items into the engine's memory."""

    op: Literal["memory.set_mode"] = Field(description="Marks this op shape.")
    mode: MemoryMode = Field(description="The new mode; `replace` is T3.")


#: One change: the shape the op's `op` member selects.
PatchOp = Annotated[
    FileEdit | PersonaAdd | PersonaRemove | SkillAdd | SkillUpdate | SkillRemove
    | ResourceAdd | ResourceRemove | MemoryAdd | MemoryUpdate | MemoryRetire | MemorySetMode,
    Field(discriminator="op"),
]


class GenomePatch(ContractModel):
    """A typed, itemized change against a base revision (contract §6); its canonical JSON's digest is the candidate id."""

    patch_schema: Literal[1] = Field(description="Version of this format; 1 for genome v1.")
    base: Digest = Field(description="The revision this patch is written against; every op must apply to it exactly.")
    ops: list[PatchOp] = Field(min_length=1, max_length=200, description="The changes, applied in order.")
    rationale: Rationale = Field(description="Why the change is proposed, in plain language for the reviewer.")
    evidence: list[EvidenceRef] = Field(max_length=MAX_EVIDENCE_REFS, description="The Experience records that motivated the patch, at most 100; required unless every op can be motivated by the rationale alone (README §6.4).")


_OPS = TypeAdapter(list[PatchOp])
_MEMORY_ITEMS = TypeAdapter(list[MemoryItem])


def parse_ops(value: Any) -> list[PatchOp]:
    """A list of ops from their JSON objects; raises ``pydantic.ValidationError``."""
    return _OPS.validate_python(value)


def parse_memory_items(value: Any) -> list[MemoryItem]:
    """The stored ``memoryItems`` list from its JSON; raises ``pydantic.ValidationError``."""
    return _MEMORY_ITEMS.validate_python(value)
