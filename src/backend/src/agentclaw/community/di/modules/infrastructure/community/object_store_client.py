"""Object-store client concern — community binding.

Capability: reading a tenant-named bucket for a manifest ``oss`` source.
Distinct from the ``object_storage`` concern next door, which binds the
platform's *own* single bucket: this one allocates a client per credential,
so there is nothing deployment-wide to configure and no config block to read.

The **factory** is the singleton; the clients it returns are not, and that is
the whole point of the seam — one apply may read two buckets under two
credentials.
"""
from __future__ import annotations

from injector import Module, provider, singleton

from agentclaw.community.log import get_logger
from agentclaw.community.plugin_api.object_store_client import (
    ObjectStoreClientFactory,
)

logger = get_logger()


class CommunityObjectStoreClientModule(Module):
    """community: boto3 against an S3-compatible endpoint."""

    @singleton
    @provider
    def object_store_client_factory(self) -> ObjectStoreClientFactory:
        from agentclaw.community.plugins.community.object_store_client import (
            S3ObjectStoreClientFactory,
        )

        # No backend switch, deliberately. The endpoint is a property of each
        # tenant credential rather than of the deployment, so there is nothing
        # here for a config block to select — where ``object_storage`` chooses
        # between fs and s3, this has one road and takes it.
        logger.info("ObjectStoreClientFactory: S3ObjectStoreClientFactory")
        return S3ObjectStoreClientFactory()
