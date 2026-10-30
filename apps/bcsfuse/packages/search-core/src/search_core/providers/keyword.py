"""Disposable sparse lexical index; durable payloads remain the source of truth.

No embedding service, downloaded tokenizer, or new database schema is needed.
ASCII words/numeric IDs and Chinese bigrams use deterministic sparse dimensions.
Qdrant applies IDF and the same payload filters before limiting results.
"""

import hashlib
import math
import re
import unicodedata
from collections import Counter
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from search_core.config.qdrant import LOCAL_QDRANT, QdrantConnection
from search_core.plugin_api.vector_point import VectorPoint
from search_core.plugin_api.vector_search_hit import VectorSearchHit
from search_core.providers.qdrant_server import remote_client


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


def sparse(text: str) -> models.SparseVector:
    terms = []
    for token in re.findall(r"[a-z0-9_]+|[\u3400-\u9fff]+", normalize(text)):
        if re.fullmatch(r"[\u3400-\u9fff]+", token) and len(token) > 1:
            terms.extend(token[i : i + 2] for i in range(len(token) - 1))
        else:
            terms.append(token)
    counts = Counter(terms)
    # Hash collisions combine weights, so indices are always unique and ordered.
    weights = Counter()
    for term, count in counts.items():
        index = int.from_bytes(
            hashlib.blake2s(term.encode(), digest_size=4).digest(), "big"
        )
        weights[index] += 1 + math.log(count)
    indices = sorted(weights)
    return models.SparseVector(indices=indices, values=[weights[i] for i in indices])


class QdrantKeywordIndex:
    """A Local or Server sparse collection, owned by the durable vector store."""

    def __init__(
        self,
        *,
        exact_match_field: str | None = None,
        connection: QdrantConnection = LOCAL_QDRANT,
        collection_name: str = "keywords",
    ):
        self._exact_match_field = exact_match_field
        self.connection = connection
        self.collection_name = collection_name
        self._client = None
        self._init_lock = Lock()
        if connection.mode == "local":
            self._ensure_client()

    def _ensure_client(self):
        if self._client is not None:
            return
        with self._init_lock:
            if self._client is not None:
                return
            client = (
                remote_client(self.connection)
                if self.connection.mode == "server"
                else QdrantClient(location=":memory:")
            )
            try:
                if not client.collection_exists(self.collection_name):
                    client.create_collection(
                        self.collection_name,
                        vectors_config={},
                        sparse_vectors_config={
                            "text": models.SparseVectorParams(
                                modifier=models.Modifier.IDF
                            )
                        },
                    )
                params = client.get_collection(self.collection_name).config.params
                sparse_config = params.sparse_vectors or {}
                if (
                    "text" not in sparse_config
                    or sparse_config["text"].modifier != models.Modifier.IDF
                ):
                    raise ValueError(
                        "Qdrant keyword collection must use the text IDF sparse vector"
                    )
            except Exception:
                client.close()
                raise
            self._client = client

    def get_vector_ids(self):
        self._ensure_client()
        ids, offset = [], None
        while True:
            points, offset = self._client.scroll(
                self.collection_name,
                offset=offset,
                limit=256,
                with_vectors=False,
            )
            ids.extend(p.payload["_keyword_external_id"] for p in points)
            if offset is None:
                return ids

    def clear(self):
        self._ensure_client()
        self._client.delete(
            self.collection_name,
            models.FilterSelector(filter=models.Filter()),
            wait=True,
        )

    def upsert(self, points: list[VectorPoint]) -> None:
        self._ensure_client()
        batch = []
        for point in points:
            payload = dict(point.payload)
            content = (
                payload.get("search_text")
                or payload.get("content")
                or payload.get("searchable_text")
                or payload.get("content_preview")
                or ""
            )
            if normalize(str(content)) in {"无", "none", "null", "n/a"}:
                content = ""
            text = payload.pop("_keyword_text", None)
            if text is None:
                text = " ".join([str(payload.get("name") or ""), str(content)])
            payload["_keyword_external_id"] = point.id
            batch.append(
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, point.id)),
                    vector={"text": sparse(text)},
                    payload=payload,
                )
            )
        if batch:
            self._client.upsert(self.collection_name, batch, wait=True)

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._ensure_client()
            self._client.delete(
                self.collection_name,
                [str(uuid5(NAMESPACE_URL, key)) for key in ids],
                wait=True,
            )

    def search(
        self,
        query: str,
        top_k: int,
        filters: dict | None,
        *,
        timeout: int | None = None,
    ) -> list[VectorSearchHit]:
        if not query.strip() or top_k <= 0:
            return []
        self._ensure_client()
        conditions = [
            models.FieldCondition(
                key=key,
                match=models.MatchAny(any=value)
                if isinstance(value, list)
                else models.MatchValue(value=value),
            )
            for key, value in (filters or {}).items()
        ]
        exact = []
        if self._exact_match_field is not None:
            # Exact IDs are retrieved separately, still subject to every caller filter.
            exact, _ = self._client.scroll(
                self.collection_name,
                scroll_filter=models.Filter(
                    must=[
                        *conditions,
                        models.FieldCondition(
                            key=self._exact_match_field,
                            match=models.MatchValue(value=normalize(query)),
                        ),
                    ]
                ),
                limit=top_k,
                with_vectors=False,
            )
        query_vector = sparse(query)
        ranked = []
        if query_vector.indices:
            ranked = self._client.query_points(
                self.collection_name,
                query=query_vector,
                using="text",
                limit=top_k,
                query_filter=models.Filter(must=conditions) if conditions else None,
                **({"timeout": timeout} if timeout is not None else {}),
            ).points
        results, seen = [], set()
        exact_score = max((p.score for p in ranked), default=0.0) + 1.0
        for point, is_exact in [
            *((p, True) for p in exact),
            *((p, False) for p in ranked),
        ]:
            payload = dict(point.payload or {})
            key = payload.pop("_keyword_external_id")
            if self._exact_match_field is not None:
                payload.pop(self._exact_match_field, None)
            if key in seen:
                continue
            seen.add(key)
            if self._exact_match_field is not None:
                payload["_keyword_exact_id"] = is_exact
            results.append(
                VectorSearchHit(
                    id=key,
                    score=exact_score if is_exact else point.score,
                    payload=payload,
                )
            )
            if len(results) >= top_k:
                break
        return results

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
