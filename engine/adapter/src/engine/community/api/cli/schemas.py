"""CLI tools router HTTP schemas.

The wire shapes are fixed by the platform caller — ``ArcaCliToolPort`` in
``apps/backend/src/agentclaw/community/core/bot_config_manifest/cli_tools/arca_port.py``
— and by the contract in
``apps/backend/docs/bot-config-manifest/engine-requirements.zh-CN.md`` §4 A2.
Field names here are that contract; renaming one breaks delivery silently on
the platform's next apply.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class InstallRequest(BaseModel):
    """``POST /api/cli/install`` — one command, bytes in base64."""

    name: str
    #: The platform sends the decoded length alongside the payload. It is
    #: informational: the engine writes what it decodes rather than trusting a
    #: caller-supplied length, so a mismatch cannot truncate a binary.
    size_bytes: int | None = None
    content_b64: str


class DeleteRequest(BaseModel):
    """``POST /api/cli/delete`` — POST, not DELETE.

    The name travels in a body, and a ``DELETE`` carrying one is refused or
    silently stripped by enough proxies that the platform does not send it.
    """

    name: str


class ReplaceToolItem(BaseModel):
    name: str
    size_bytes: int | None = None
    content_b64: str


class ReplaceRequest(BaseModel):
    """``POST /api/cli/replace`` — the body **is** the desired command set.

    An empty list is a real request meaning "this bot has no commands", not a
    no-op, so ``tools`` defaults to empty rather than being required.
    """

    tools: list[ReplaceToolItem] = Field(default_factory=list)


class AcliInstallRequest(BaseModel):
    """``POST /api/cli/acli/install`` — install one tool from the acli market.

    ``install_dir`` comes from the platform (the manifest's
    ``managed_bin_dir``) rather than from engine config, so it is the same
    directory the container start-up already puts on the engine's ``PATH``.
    """

    name: str
    install_dir: str


class AcliUninstallRequest(BaseModel):
    """``POST /api/cli/acli/uninstall`` — remove a tool acli installed."""

    name: str
    install_dir: str


__all__ = [
    "AcliInstallRequest",
    "AcliUninstallRequest",
    "DeleteRequest",
    "InstallRequest",
    "ReplaceRequest",
    "ReplaceToolItem",
]
