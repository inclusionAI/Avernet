"""Request/response models for the identity (bot identity files) group."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class IdentityFileType(StrEnum):
    """Which identity file an operation addresses.

    Each member names one markdown file in the bot's identity directory, stored
    as `<TYPE>.md`. The set is fixed — a type not listed here is refused.
    """

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


_TYPE_DESC = "Which identity file this is; the file is stored as `<TYPE>.md`."

_PATH_DESC = (
    "Path of the file within the bot's workspace. Read-only — it is derived "
    "from the type, not chosen by the caller."
)


class IdentityFileInfo(BaseModel):
    """One identity file, and whether the bot has it."""

    type: IdentityFileType = Field(description=_TYPE_DESC)
    exists: bool = Field(
        description="False when the bot has no such file yet. Writing one "
        "creates it."
    )
    file_path: str = Field(description=_PATH_DESC)


class IdentityFileList(BaseModel):
    """Every identity file a bot can have, and whether each exists."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "bot_id": "20260810_q5o4c89g",
                "files": [
                    {
                        "type": "RULES",
                        "exists": True,
                        "file_path": "identity/RULES.md",
                    },
                    {"type": "OKR", "exists": False, "file_path": "identity/OKR.md"},
                ],
            }
        }
    )

    bot_id: str = Field(description="Bot these files belong to.")
    files: list[IdentityFileInfo] = Field(
        description="One entry per possible identity file, present whether or "
        "not the file exists."
    )


class IdentityFile(BaseModel):
    """A single identity file's content."""

    type: IdentityFileType = Field(description=_TYPE_DESC)
    bot_id: str = Field(description="Bot this file belongs to.")
    content: str = Field(
        description="The file's full markdown content; empty when the file does "
        "not exist yet."
    )
    file_path: str = Field(description=_PATH_DESC)


class IdentityFileRef(BaseModel):
    """Reference to an identity file, returned after a write."""

    # The content is not echoed: the caller just sent it, and an identity file
    # is large enough that repeating it doubles the write's cost for nothing.

    type: IdentityFileType = Field(description=_TYPE_DESC)
    bot_id: str = Field(description="Bot this file belongs to.")
    file_path: str = Field(description=_PATH_DESC)


class IdentityFileWrite(BaseModel):
    """Write-an-identity-file request body."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "content": "# Rules\n\n- Answer in the user's language.\n"
                "- Ask before deleting anything.\n"
            }
        }
    )

    content: str = Field(
        description="The file's full markdown content. This **replaces** the "
        "file — send the whole document, not a fragment to append. Send an "
        "empty string to blank the file."
    )
