"""Config seam for the content store (W11, #1510).

The store's medium and its root directory are ``application.yaml`` decisions, kept
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
_CONTENT_STORE_BACKEND_KEY = "content_store_backend"

#: The two media the blobs can live on (``blobs.py``): the deployment's
#: object storage, which every backend instance shares, or the local tree
#: under ``content_store_dir``, which only the instance that wrote it reads
#: unless the directory is a volume all of them mount.
BACKEND_OBJECT_STORAGE = "object_storage"
BACKEND_FS = "fs"
CONTENT_STORE_BACKENDS = (BACKEND_OBJECT_STORAGE, BACKEND_FS)

#: Object storage by default: a deployment with more than one backend
#: instance is correct without an overlay line, and a single-machine install
#: lands on its filesystem object storage. ``fs`` is the explicit opt-out.
DEFAULT_CONTENT_STORE_BACKEND = BACKEND_OBJECT_STORAGE


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


def content_store_backend_from_config(settings: Mapping[str, Any]) -> str:
    """Where the blobs live, from the merged ``user_config`` tree: ``"object_storage"`` or ``"fs"``.

    The key is optional and defaults to :data:`DEFAULT_CONTENT_STORE_BACKEND`.
    Any other value raises ``ValueError``: a typo must stop the process at
    startup rather than quietly choose a medium.
    """
    block = settings.get(_MANIFEST_BLOCK)
    if block is None:
        return DEFAULT_CONTENT_STORE_BACKEND
    if not isinstance(block, Mapping):
        raise ValueError(
            f"user_config.{_MANIFEST_BLOCK} must be a mapping, "
            f"got {type(block).__name__}"
        )
    backend = block.get(_CONTENT_STORE_BACKEND_KEY, DEFAULT_CONTENT_STORE_BACKEND)
    if backend not in CONTENT_STORE_BACKENDS:
        raise ValueError(
            f"user_config.{_MANIFEST_BLOCK}.{_CONTENT_STORE_BACKEND_KEY} "
            f"must be one of {', '.join(CONTENT_STORE_BACKENDS)}, got {backend!r}"
        )
    return backend
