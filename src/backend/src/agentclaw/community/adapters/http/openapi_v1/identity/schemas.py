"""Request/response models for the identity (bot identity files) group."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class IdentityFileType(StrEnum):
    """Whitelisted identity-file types (physical file is ``<type>.md``)."""

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


class IdentityFileInfo(BaseModel):
    """One identity file's presence within a bot."""

    type: IdentityFileType
    exists: bool
    file_path: str


class IdentityFileList(BaseModel):
    """All possible identity files for a bot and whether each exists."""

    bot_id: str
    files: list[IdentityFileInfo]


class IdentityFile(BaseModel):
    """A single identity file's content."""

    type: IdentityFileType
    bot_id: str
    content: str
    file_path: str


class IdentityFileRef(BaseModel):
    """Reference to an identity file (returned after a write)."""

    type: IdentityFileType
    bot_id: str
    file_path: str


class IdentityFileWrite(BaseModel):
    """Write-an-identity-file request body."""

    content: str
