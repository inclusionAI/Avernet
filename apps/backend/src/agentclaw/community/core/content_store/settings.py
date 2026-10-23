"""Config seam for the manifest content store (W11, #1510).

The store's root directory is an ``application.yaml`` decision, kept
config-borne per the review ruling on the transport allowlist (the same
reasoning, one wave later): an env var is image-involving and implicit, and
raw environment access belongs to config loading and composition roots
(AGENTS.md), never to core. This module is the pure parser over the
already-loaded ``user_config`` tree; the composition root that will
construct the service (W4's orchestrator wiring) reads through it and hands
the resulting :class:`~pathlib.Path` to the service constructor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

#: The blob root's neutral default, relative to the process working
#: directory — the same shape as ``user_config.skill_scan.storage_dir``.
#: A deployment that wants a shared volume points the key at an absolute
#: NAS path in its own env overlay; the neutral base ships bare.
DEFAULT_CONTENT_STORE_DIR = "./data/manifest_content"

#: Where the key lives in the YAML tree, as one constant: the block name
#: under ``user_config`` and the key inside it.
_MANIFEST_BLOCK = "bot_config_manifest"
_CONTENT_STORE_DIR_KEY = "content_store_dir"


def content_store_root_from_config(settings: Mapping[str, Any]) -> Path:
    """The content store root from the merged ``user_config`` tree.

    The key is optional and defaults to :data:`DEFAULT_CONTENT_STORE_DIR`;
    an explicitly malformed value (not a string) is a configuration error
    and raises ``ValueError`` — a typo must fail its reader loudly rather
    than send 100-MiB blobs somewhere nobody chose. Path resolution
    (expanduser, cwd-relative) is the service's job at construction, so the
    value recorded in the effective-config snapshots stays exactly what the
    yaml says.
    """
    block = settings.get(_MANIFEST_BLOCK)
    if block is None:
        return Path(DEFAULT_CONTENT_STORE_DIR)
    if not isinstance(block, Mapping):
        raise ValueError(
            f"user_config.{_MANIFEST_BLOCK} must be a mapping, "
            f"got {type(block).__name__}"
        )
    root = block.get(_CONTENT_STORE_DIR_KEY, DEFAULT_CONTENT_STORE_DIR)
    if not isinstance(root, str) or not root.strip():
        raise ValueError(
            f"user_config.{_MANIFEST_BLOCK}.{_CONTENT_STORE_DIR_KEY} "
            f"must be a non-empty string, got {root!r}"
        )
    return Path(root)
