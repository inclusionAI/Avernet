"""TC keyword text and Worker-ID priority around shared sparse retrieval."""

from search_core.plugin_api.vector_point import VectorPoint
from search_core.providers.keyword import QdrantKeywordIndex as SharedKeywordIndex
from search_core.providers.keyword import normalize, sparse


class QdrantKeywordIndex(SharedKeywordIndex):
    def __init__(self):
        super().__init__(exact_match_field="_keyword_worker_id")

    def upsert(self, points: list[VectorPoint]) -> None:
        prepared = []
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
            payload["_keyword_text"] = " ".join(
                [
                    worker_id,
                    str(payload.get("identity_name") or ""),
                    str(payload.get("name") or ""),
                    str(content),
                ]
            )
            payload["_keyword_worker_id"] = normalize(worker_id)
            prepared.append(
                VectorPoint(id=point.id, vector=point.vector, payload=payload)
            )
        super().upsert(prepared)
