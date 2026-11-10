"""Qdrant native BM25 with multilingual text and standard name ngrams.

BM25 and best-field scoring run on the same Qdrant Server as dense retrieval.
Python Local is rejected explicitly; there is no alternate scoring fallback.
"""

import unicodedata
from threading import Lock
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import models
from whoosh.analysis import LowercaseFilter, NgramFilter, RegexTokenizer

from search_core.config.qdrant import LOCAL_QDRANT, QdrantConnection
from search_core.plugin_api.vector_point import VectorPoint
from search_core.plugin_api.vector_search_hit import VectorSearchHit
from search_core.providers.qdrant_server import remote_client


_FIELDS = ("_bm25_name", "_bm25_text", "_bm25_name_grams")
_NAME_ANALYZER = (
    RegexTokenizer(r"[^\W_]+") | LowercaseFilter() | NgramFilter(minsize=3, maxsize=3)
)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip().casefold()


def name_grams(text: str) -> str:
    """Mature tokenizer only; native BM25 owns frequencies and length statistics."""
    return " ".join(token.text for token in _NAME_ANALYZER(normalize(text)))


class QdrantKeywordIndex:
    """A native full-text collection, owned by the durable vector store."""

    def __init__(
        self,
        *,
        exact_match_field: str | None = None,
        connection: QdrantConnection = LOCAL_QDRANT,
        collection_name: str = "keywords",
    ):
        if connection.mode != "server":
            raise ValueError(
                "Native multilingual BM25 requires Qdrant Server >=1.20; Python Local "
                "is unsupported. Set QDRANT_MODE=server and QDRANT_URL explicitly."
            )
        self._exact_match_field = exact_match_field
        self.connection = connection
        self.collection_name = collection_name
        self._client = None
        self._init_lock = Lock()

    def _ensure_client(self):
        if self._client is not None:
            return
        with self._init_lock:
            if self._client is not None:
                return
            client = remote_client(self.connection)
            try:
                if not client.collection_exists(self.collection_name):
                    client.create_collection(self.collection_name, vectors_config={})
                schema = client.get_collection(self.collection_name).payload_schema
                for field in _FIELDS:
                    params = models.TextIndexParams(
                        type=models.TextIndexType.TEXT,
                        tokenizer=(
                            models.TokenizerType.WHITESPACE
                            if field == _FIELDS[2]
                            else models.TokenizerType.MULTILINGUAL
                        ),
                        scoring=models.TextScoringParams(
                            type=models.TextScoringType.BM25
                        ),
                    )
                    existing = schema.get(field)
                    if existing is None:
                        client.create_payload_index(
                            self.collection_name, field, params, wait=True
                        )
                    elif existing.params != params:
                        raise ValueError(
                            f"Incompatible BM25 index {field}; rebuild keyword index"
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
                text = str(content)
            payload["_keyword_external_id"] = point.id
            name = str(payload.get("name") or "")
            payload.update(zip(_FIELDS, (name, str(text), name_grams(name))))
            batch.append(
                models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, point.id)),
                    vector={},
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
        query_filter = models.Filter(must=conditions) if conditions else None
        texts = (query, query, name_grams(query))
        prefetch = [
            models.Prefetch(
                query=models.TextQuery(text=text),
                using=field,
                filter=query_filter,
                limit=top_k,
            )
            for field, text in zip(_FIELDS, texts)
            if text.strip()
        ]
        ranked = self._client.query_points(
            self.collection_name,
            prefetch=prefetch,
            query=models.FormulaQuery(
                formula=models.MaxExpression(
                    max=[f"$score[{i}]" for i in range(len(prefetch))]
                )
            ),
            limit=top_k,
            query_filter=query_filter,
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
            for field in _FIELDS:
                payload.pop(field, None)
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
