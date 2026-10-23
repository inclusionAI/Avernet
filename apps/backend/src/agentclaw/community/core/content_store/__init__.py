"""The content store: the platform's own copy of what it fetched or produced.

Bytes in a content-addressed blob tree, provenance in ``ac_manifest_content``,
one component behind every consumer: the Bot Config Manifest files what it
fetches and reads the copy for delivery, audit and ``keep_last``; the Genome
Registry files what it produces and reads it back scoped by the rows it
filed. See each module for its half:

- ``service.py`` — the one funnel: store/read/records, digest as address.
- ``service_protocol.py`` — the contract both consumers inject.
- ``models.py`` — ORM row + records + the bot scope.
- ``limits.py`` — the address grammar and the audit identity width.
- ``tree.py`` — acm-tree-v1, the framing a stored tree is.
- ``settings.py`` — the yaml-borne root, parsed purely for the composition
  root to inject.
- ``errors.py`` — the refusal vocabulary.
"""
from agentclaw.community.core.content_store.errors import (
    ContentIntegrityError,
    ContentMissingError,
    ContentStoreError,
    ContentStoreFault,
)
from agentclaw.community.core.content_store.limits import (
    DIGEST_RE,
    MAX_AUDIT_ACTOR_CHARS,
)
from agentclaw.community.core.content_store.models import (
    ContentScope,
    ManifestContentModel,
    StoredContentRecord,
)
from agentclaw.community.core.content_store.service import (
    ContentStoreService,
)
from agentclaw.community.core.content_store.service_protocol import (
    ContentStoreProtocol,
)
from agentclaw.community.core.content_store.settings import (
    DEFAULT_CONTENT_STORE_DIR,
    content_store_root_from_config,
)
from agentclaw.community.core.content_store.tree import (
    TREE_MAGIC,
    canonical_tree_bytes,
    decode_tree_bytes,
)

__all__ = [
    "ContentIntegrityError",
    "ContentMissingError",
    "ContentScope",
    "ContentStoreError",
    "ContentStoreFault",
    "ContentStoreProtocol",
    "ContentStoreService",
    "DEFAULT_CONTENT_STORE_DIR",
    "DIGEST_RE",
    "MAX_AUDIT_ACTOR_CHARS",
    "ManifestContentModel",
    "StoredContentRecord",
    "TREE_MAGIC",
    "canonical_tree_bytes",
    "content_store_root_from_config",
    "decode_tree_bytes",
]
