"""Run retrieval tests against an explicitly supplied, isolated Qdrant Server."""

import os

import pytest
from qdrant_client import QdrantClient


@pytest.fixture(scope="session")
def qdrant_test_client():
    url = os.environ.get("QDRANT_TEST_URL")
    if not url:
        yield None
        return
    client = QdrantClient(url=url, prefer_grpc=False)
    try:
        yield client
    finally:
        client.close()


@pytest.fixture(autouse=True)
def qdrant_server_environment(monkeypatch, qdrant_test_client):
    url = os.environ.get("QDRANT_TEST_URL")
    if not url:
        yield
        return
    monkeypatch.setenv("QDRANT_MODE", "server")
    monkeypatch.setenv("QDRANT_URL", url)
    monkeypatch.setenv("QDRANT_PREFER_GRPC", "false")
    # This URL must identify a test-only server; remove only collections created
    # by this test, after application fixtures have closed their clients.
    client = qdrant_test_client
    existing = {c.name for c in client.get_collections().collections}
    yield
    # Restore test network guards before talking to the test server at teardown.
    monkeypatch.undo()
    created = {c.name for c in client.get_collections().collections} - existing
    for name in created:
        client.delete_collection(name)
