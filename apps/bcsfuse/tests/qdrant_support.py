"""Real Qdrant Server configuration for retrieval tests (no Local fallback)."""

import os
from hashlib import sha256
from uuid import uuid4

from search_core.config.qdrant import QdrantConnection


def server_connection():
    url = os.environ.get("QDRANT_TEST_URL", "")
    if not url:
        raise RuntimeError(
            "Retrieval tests require QDRANT_TEST_URL for Qdrant Server >=1.20"
        )
    return QdrantConnection(mode="server", url=url, prefer_grpc=False)


def server_options(path=None, *, collection_name=None):
    name = collection_name or (
        "test_" + (sha256(str(path).encode()).hexdigest()[:24] if path else uuid4().hex)
    )
    return {"connection": server_connection(), "collection_name": name}
