"""Standalone loopback catalog: SQLite, Qdrant Local and offline demo vectors."""

import argparse
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware

from search_core.api.models import VerifiedIdentity
from search_core.bootstrap.catalog import create_catalog_search_app
from search_core.config.qdrant import LOCAL_QDRANT, QdrantConnection
from search_core.providers.demo_embedding import DemoEmbedding
from search_core.providers.qdrant_index import QdrantRetrievalIndex
from search_core.providers.sql_catalog import sqlite_catalog_repository


async def local_identity(request: Request):
    if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
        raise HTTPException(403, "local catalog permits loopback clients only")
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "cross-origin access to local catalog denied")
    return VerifiedIdentity("local-operator")


def create_local_app(
    data_dir: Path,
    *,
    poll_seconds=1.0,
    connection=LOCAL_QDRANT,
    document_converter=None,
):
    data_dir = Path(data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    repository = sqlite_catalog_repository(str(data_dir / "cards.sqlite3"))
    embedding = DemoEmbedding()
    # Bind the physical collection to the same configuration as the Doc namespace.
    from search_core.catalog.updates import configuration_index_key

    key = configuration_index_key(
        "offline-demo-hash-v1/256",
        {"resource_type"},
        document_converter,
    )
    index = QdrantRetrievalIndex(
        path=str(data_dir / "qdrant"),
        dimension=embedding.dimension,
        collection_name=f"catalog_{key}",
        connection=connection,
    )
    try:
        app = create_catalog_search_app(
            repository,
            index,
            embedding,
            local_identity,
            local_identity,
            embedding_key="offline-demo-hash-v1/256",
            instance_id="local",
            poll_seconds=poll_seconds,
            document_converter=document_converter,
        )
    except Exception:
        index.close()
        raise
    inner_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            async with inner_lifespan(application):
                yield
        finally:
            await asyncio.to_thread(index.close)

    app.router.lifespan_context = lifespan
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
    )
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(".data/catalog"))
    parser.add_argument("--port", type=int, default=8877)
    parser.add_argument(
        "--qdrant-url",
        default="",
        help="Use an existing Qdrant Server; otherwise use Local",
    )
    args = parser.parse_args()
    connection = (
        QdrantConnection(mode="server", url=args.qdrant_url)
        if args.qdrant_url
        else LOCAL_QDRANT
    )
    import uvicorn

    uvicorn.run(
        create_local_app(args.data_dir, connection=connection),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
