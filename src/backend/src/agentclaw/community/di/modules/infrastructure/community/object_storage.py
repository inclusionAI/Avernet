"""Object-storage concern — community binding.

Capability: object storage. B3 binds the filesystem impl (default) or the
S3-compatible impl, selected by the ``object_storage.backend`` config. The
``CommunityObjectStorageConfig`` provider lives here (community-only) and reads
the ``object_storage`` block of ``user_config`` — corp/test never resolve it.
"""
from __future__ import annotations

from injector import Module, inject, provider, singleton

from agentclaw.community.di import config_community as cfg
from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_storage import ObjectStoragePlugin


logger = get_logger()


class CommunityObjectStorageModule(Module):
    """community: filesystem (default) or S3-compatible object storage."""

    @singleton
    @provider
    def object_storage_config(self) -> cfg.CommunityObjectStorageConfig:
        """Read the ``object_storage`` block (incl. nested ``s3``); fall back to
        dataclass defaults."""
        from agentclaw.community.di.modules.config_module import _block

        block = _block("object_storage")
        defaults = cfg.CommunityObjectStorageConfig()

        s3_block = block.get("s3") or {}
        s3_defaults = cfg.CommunityS3Config()
        s3 = cfg.CommunityS3Config(
            endpoint=s3_block.get("endpoint", s3_defaults.endpoint),
            bucket=s3_block.get("bucket", s3_defaults.bucket),
            region=s3_block.get("region", s3_defaults.region),
            secure=s3_block.get("secure", s3_defaults.secure),
        )
        return cfg.CommunityObjectStorageConfig(
            backend=block.get("backend", defaults.backend),
            fs_root=block.get("fs_root", defaults.fs_root),
            s3=s3,
        )

    @singleton
    @provider
    @inject
    def object_storage(
        self, config: cfg.CommunityObjectStorageConfig
    ) -> ObjectStoragePlugin:
        from agentclaw.community.plugins.community.object_storage import (
            CommunityFsObjectStorage,
            CommunityS3ObjectStorage,
        )

        backend = config.backend.strip().lower()
        if backend == "s3":
            logger.info("ObjectStoragePlugin: CommunityS3ObjectStorage")
            return CommunityS3ObjectStorage(config.s3)
        if backend == "fs":
            logger.info(
                "ObjectStoragePlugin: CommunityFsObjectStorage (root=%s)",
                config.fs_root,
            )
            return CommunityFsObjectStorage(config.fs_root)
        # Fail fast: an unrecognized backend must not silently fall back to the
        # filesystem and write objects to local disk instead of the bucket.
        raise ValueError(
            f"Unknown object_storage.backend {config.backend!r} "
            "(expected 'fs' or 's3')."
        )
