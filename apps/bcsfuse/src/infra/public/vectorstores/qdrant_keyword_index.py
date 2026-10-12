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
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models

from src.domain.models.vector_point import VectorPoint
from src.domain.models.vector_search_hit import VectorSearchHit


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
    """An in-process Qdrant sparse collection, owned by the durable vector store."""

    def __init__(self):
        self._client = QdrantClient(location=":memory:")
        self._client.create_collection(
            "keywords",
            vectors_config={},
            sparse_vectors_config={
                "text": models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )

    def upsert(self, points: list[VectorPoint]) -> None:
        batch = []
        for point in points:
            payload = dict(point.payload)
            content = (
                payload.get("content")
                or payload.get("searchable_text")
                or payload.get("content_preview")
                or ""
            )
            if normalize(str(content)) in {"无", "none", "null", "n/a"}:
                content = ""
            worker_id = str(payload.get("worker_id") or "")
            text = " ".join(
                [
                    worker_id,
                    str(payload.get("identity_name") or ""),
                    str(payload.get("name") or ""),
                    str(content),
                ]
            )
            payload["_keyword_external_id"] = point.id
            payload["_keyword_worker_id"] = normalize(worker_id)
            batch.append(
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, point.id)),
                    vector={"text": sparse(text)},
                    payload=payload,
                )
            )
        if batch:
            self._client.upsert("keywords", batch)

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._client.delete(
                "keywords", [str(uuid5(NAMESPACE_URL, key)) for key in ids]
            )

    def search(
        self, query: str, top_k: int, filters: dict | None
    ) -> list[VectorSearchHit]:
        if not query.strip() or top_k <= 0:
            return []
        conditions = [
            models.FieldCondition(
                key=key,
                match=models.MatchAny(any=value)
                if isinstance(value, list)
                else models.MatchValue(value=value),
            )
            for key, value in (filters or {}).items()
        ]
        # Exact IDs are retrieved separately, still subject to every caller filter.
        exact, _ = self._client.scroll(
            "keywords",
            scroll_filter=models.Filter(
                must=[
                    *conditions,
                    models.FieldCondition(
                        key="_keyword_worker_id",
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
                "keywords",
                query=query_vector,
                using="text",
                limit=top_k,
                query_filter=models.Filter(must=conditions) if conditions else None,
            ).points
        results, seen = [], set()
        exact_score = max((p.score for p in ranked), default=0.0) + 1.0
        for point, is_exact in [
            *((p, True) for p in exact),
            *((p, False) for p in ranked),
        ]:
            payload = dict(point.payload or {})
            key = payload.pop("_keyword_external_id")
            payload.pop("_keyword_worker_id", None)
            if key in seen:
                continue
            seen.add(key)
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
        self._client.close()
