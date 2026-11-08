"""Public manifest capability-discovery response models for the bots group."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ManifestConstruct(BaseModel):
    """One thing a manifest can declare, and whether this bot accepts it."""

    kind: str = Field(
        description=(
            "`category` (a current execution category under `manifest`), `section` (a "
            "top-level section such as `script`)."
        )
    )
    name: str = Field(description="The construct's name within its kind.")
    supported: bool = Field(
        description="False when a document using this construct is refused."
    )
    reason: str = Field(description="Empty when supported; otherwise names the cause.")


class ManifestSourceCell(BaseModel):
    """One (category, protocol) combination, and whether this bot accepts it.

    The cartesian view `constructs` structurally cannot express: its rows carry
    one verdict per construct, so "which categories work with which sources" had
    no representation at all and callers discovered the answer from a 422 — or,
    worse, from a failed apply. Read this to decide what to write before writing
    it.
    """

    category: str = Field(description="A current execution category under `manifest`.")
    protocol: str = Field(
        description=(
            "How the content travels: `content` (inline in the document), "
            "`oss` (an object over https, signed or plain) or `git` (a "
            "repository resolved to one commit). Reaching a source **by name** "
            "(`from`) is not a fourth value — a named source declares one of "
            "these, and that protocol's row is the one that governs."
        )
    )
    supported: bool = Field(
        description="False when a document using this combination is refused."
    )
    reason: str = Field(
        description="Empty when supported; otherwise the refusal a `PUT` gives."
    )


class ManifestSkillReference(BaseModel):
    """Support for one Skill reference form, shared by Direct and SkillSet entries."""

    reference: Literal["package", "repo_path", "skill_uuid", "skill_code"] = Field(
        description="Local package, shared Repo path, Center UUID, or public Center code."
    )
    supported: bool = Field(description="False when this bot refuses the reference form.")
    reason: str = Field(description="Empty when supported; otherwise the validator's refusal.")


class ConfigManifestCapabilities(BaseModel):
    """Which manifest constructs a bot accepts.

    Answered from the same resolver the write path refuses with, so this can
    never claim support for something a `PUT` then rejects.
    """

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "engine_type": "teclaw",
                "bot_type": "personal",
                "schema_versions": [1],
                "constructs": [
                    {
                        "kind": "category",
                        "name": "capabilities",
                        "supported": True,
                        "reason": "",
                    },
                    {
                        "kind": "section",
                        "name": "script",
                        "supported": False,
                        "reason": "teclaw bots are provisioned without a "
                        "start sequence, so a script would never execute",
                    },
                ],
                "skill_references": [
                    {"reference": "package", "supported": True, "reason": ""},
                    {"reference": "repo_path", "supported": True, "reason": ""},
                    {"reference": "skill_uuid", "supported": True, "reason": ""},
                    {"reference": "skill_code", "supported": True, "reason": ""},
                ],
                "source_matrix": [
                    {
                        "category": "resources",
                        "protocol": "git",
                        "supported": True,
                        "reason": "",
                    },
                    {
                        "category": "capabilities",
                        "protocol": "content",
                        "supported": False,
                        "reason": "a skills entry is a package (SKILL.md + "
                        "the files it names) — inline content cannot be one; "
                        "declare a source with 'protocol: git' or "
                        "'protocol: oss'",
                    },
                ],
            }
        }
    )

    bot_id: str = Field(description="The bot these capabilities describe.")
    engine_type: str = Field(description="The engine the answer was computed for.")
    bot_type: str = Field(description="The bot type the answer was computed for.")
    schema_versions: list[int] = Field(
        description="The `schema_version` values this deployment accepts."
    )
    constructs: list[ManifestConstruct] = Field(
        description="Every construct, supported or not, with its reason."
    )
    skill_references: list[ManifestSkillReference] = Field(
        default_factory=list,
        description=(
            "The four Skill reference forms for this bot and engine. Direct and "
            "SkillSet entries use the same verdict. Asset access and availability "
            "are checked during resolution. Git package transport support does "
            "not imply shared Repo reference support."
        )
    )
    source_matrix: list[ManifestSourceCell] = Field(
        default_factory=list,
        description=(
            "Every current (category, protocol) combination, supported or not. "
            "For capabilities this describes Local Skill package transport, "
            "not shared Repo or Center reference support. "
            "Additive alongside `constructs`, which keeps its exact shape and "
            "meaning: a construct row says whether a *form* can be resolved at "
            "all, and a matrix cell says whether a *combination* is delivered."
        ),
    )
