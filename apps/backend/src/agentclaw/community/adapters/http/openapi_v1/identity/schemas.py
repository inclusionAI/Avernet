"""Request/response models for the identity (bot identity files) group.

Docstrings and field descriptions here are published verbatim into the OpenAPI
document external tenants read — keep them caller-facing prose. Rationale and
internal names belong in ``#`` comments.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentclaw.community.adapters.http.openapi_v1.enums import _DocumentedEnum


class IdentityFileType(_DocumentedEnum):
    """The identity files a bot can carry (each stored as `<TYPE>.md`)."""

    # A closed whitelist at the source: the identity service accepts exactly
    # these file names and nothing else, so a documented enum is safe on both
    # request and response. The per-member texts describe how the platform and
    # the engines actually use each file; GREETING and README are accepted and
    # stored but read only by engines/clients, not by the platform itself.

    RULES = "RULES"
    OKR = "OKR"
    SAFETY = "SAFETY"
    SOUL = "SOUL"
    OUTPUT = "OUTPUT"
    MEMORY = "MEMORY"
    IDENTITY = "IDENTITY"
    AGENTS = "AGENTS"
    USER = "USER"
    TOOLS = "TOOLS"
    HEARTBEAT = "HEARTBEAT"
    BOOTSTRAP = "BOOTSTRAP"
    KNOWLEDGE = "KNOWLEDGE"
    CLAUDE = "CLAUDE"
    GREETING = "GREETING"
    README = "README"

    __descriptions__ = {
        "RULES": "The bot's hard operating rules — constraints it must always "
        "follow and lines it must never cross.",
        "OKR": "The bot's objectives and key results — the goals it works "
        "toward and is measured against.",
        "SAFETY": "Safety boundaries and conduct rules — data the bot may not "
        "touch and harms it must warn about.",
        "SOUL": "The bot's persona — temperament, tone, communication style "
        "and behavioural principles.",
        "OUTPUT": "The required shape of the bot's answers — its output "
        "format contract.",
        "MEMORY": "Durable facts the bot carries between sessions; typically "
        "written by the bot itself rather than authored by hand.",
        "IDENTITY": "The bot's identity card — structured name, display name, "
        "emoji and role, which other services parse to recognise the bot.",
        "AGENTS": "The main instruction file the bot's engine boots from; on "
        "some engines the platform maintains a reference section in it that "
        "points at the other identity files.",
        "USER": "Who the bot serves and how to talk to them — its default "
        "audience and interaction posture.",
        "TOOLS": "The capabilities the bot may use and the boundaries on "
        "them.",
        "HEARTBEAT": "The bot's periodic self-check — what to re-verify each "
        "cycle and what to do when something is off.",
        "BOOTSTRAP": "The bot's startup sequence — which identity files to "
        "read in what order, and the first action after boot.",
        "KNOWLEDGE": "The bot's domain knowledge and its source-priority "
        "rules — which inputs win over which.",
        "CLAUDE": "The instruction file consumed by bots on the Claude Code "
        "engine.",
        "GREETING": "The bot's opening greeting text; read by engines and "
        "clients, not by the platform.",
        "README": "Free-form human documentation about the bot.",
    }


# What `file_path` means, stated once: it is informational — the file is
# addressed by its type, never by this path.
_FILE_PATH_DESC = (
    "Where the file lives inside the bot's identity namespace, e.g. "
    "'identity/RULES.md'. Informational — address the file by its type, not "
    "by this path."
)


class IdentityFileInfo(BaseModel):
    """One identity file's presence within a bot."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "RULES",
                "exists": True,
                "file_path": "identity/RULES.md",
            }
        }
    )

    type: IdentityFileType = Field(description="Which identity file this is.")
    # Derived from content presence, so empty-but-existing is reported False —
    # stated in the description because a client cannot tell the two apart.
    exists: bool = Field(
        description="True when the file has content. A file that exists but "
        "is empty reports false — empty and absent are indistinguishable."
    )
    file_path: str = Field(description=_FILE_PATH_DESC)


class IdentityFileList(BaseModel):
    """All possible identity files for a bot and whether each exists."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260813_a7k2m9p1",
                "files": [
                    {
                        "type": "RULES",
                        "exists": True,
                        "file_path": "identity/RULES.md",
                    },
                    {
                        "type": "SOUL",
                        "exists": False,
                        "file_path": "identity/SOUL.md",
                    },
                ],
            }
        }
    )

    bot_id: str = Field(description="The bot the listing describes.")
    files: list[IdentityFileInfo] = Field(
        description="One entry per known file type. Order is not guaranteed — "
        "key off each entry's type."
    )


class IdentityFile(BaseModel):
    """A single identity file's content."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "RULES",
                "bot_id": "20260813_a7k2m9p1",
                "content": "# Rules\n- Never contact customers directly.\n",
                "file_path": "identity/RULES.md",
            }
        }
    )

    type: IdentityFileType = Field(description="Which identity file this is.")
    bot_id: str = Field(description="The bot the file belongs to.")
    content: str = Field(
        description="Full markdown content. Empty string when the file has "
        "never been written — reading a missing file is not an error."
    )
    file_path: str = Field(description=_FILE_PATH_DESC)


class IdentityFileRef(BaseModel):
    """Reference to an identity file (returned after a write)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "type": "RULES",
                "bot_id": "20260813_a7k2m9p1",
                "file_path": "identity/RULES.md",
            }
        }
    )

    type: IdentityFileType = Field(description="Which identity file was written.")
    bot_id: str = Field(description="The bot the file belongs to.")
    file_path: str = Field(description=_FILE_PATH_DESC)


class IdentityFileWrite(BaseModel):
    """Write-an-identity-file request body."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"content": "# Rules\n- Never contact customers directly.\n"}
        }
    )

    content: str = Field(
        description="Full replacement content (markdown). The write creates "
        "the file when absent and overwrites it entirely otherwise; send an "
        "empty string to clear the file."
    )
