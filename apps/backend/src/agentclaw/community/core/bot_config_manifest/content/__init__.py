"""Platform-side materialization and retention of manifest content (W11).

Fetched manifest sources get a durable platform copy — bytes in a
content-addressed blob tree, provenance in ``ac_manifest_content`` — and
every step after fetch reads that copy (§2.8). See each module for its half:

- ``service.py`` — the one funnel: store/read/records, digest as address.
- ``models.py`` — ORM row + records + the bot scope.
- ``settings.py`` — the yaml-borne root, parsed purely for the composition
  root (W4) to inject.
- ``errors.py`` — the refusal vocabulary.
"""
from agentclaw.community.core.bot_config_manifest.content.errors import (
    ContentIntegrityError,
    ContentMissingError,
    ContentStoreError,
)
from agentclaw.community.core.bot_config_manifest.content.models import (
    ContentScope,
    ManifestContentModel,
    StoredContentRecord,
)
from agentclaw.community.core.bot_config_manifest.content.service import (
    ManifestContentService,
)
from agentclaw.community.core.bot_config_manifest.content.service_protocol import (
    ManifestContentServiceProtocol,
)
from agentclaw.community.core.bot_config_manifest.content.settings import (
    DEFAULT_CONTENT_STORE_DIR,
    content_store_root_from_config,
)

__all__ = [
    "ContentIntegrityError",
    "ContentMissingError",
    "ContentScope",
    "ContentStoreError",
    "DEFAULT_CONTENT_STORE_DIR",
    "ManifestContentModel",
    "ManifestContentService",
    "ManifestContentServiceProtocol",
    "StoredContentRecord",
    "content_store_root_from_config",
]
