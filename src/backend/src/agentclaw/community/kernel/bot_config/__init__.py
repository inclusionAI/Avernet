"""Published bot-config contract (``BotConfigArtifact``).

This package holds the **published, cross-boundary** contract an external
engine consumes to boot a bot. It lives in ``kernel`` — the lowest,
dependency-free layer — so any layer (``core`` builds it, ``plugins``
serialize it, ``api`` may expose it) can depend on it *downward* without a
back-dependency.

It is a *contract*, not an internal model: the field names are the external
API. The language-neutral source of truth is ``artifact.schema.json`` beside
``artifact.py``; contract evolution goes through ``schema_version`` (distinct
from the per-bot content ``version``).
"""
from .artifact import (  # noqa: F401  (relative intra-kernel import — no cross-layer dep)
    SCHEMA_VERSION,
    BotConfigArtifact,
    FileRef,
    McpManifest,
    McpServerRef,
    ResourceRef,
    SkillRef,
    StoreRef,
)


__all__ = [
    "SCHEMA_VERSION",
    "BotConfigArtifact",
    "FileRef",
    "McpManifest",
    "McpServerRef",
    "ResourceRef",
    "SkillRef",
    "StoreRef",
]
