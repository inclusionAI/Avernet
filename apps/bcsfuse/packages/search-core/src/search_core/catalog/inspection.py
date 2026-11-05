"""Operator previews of stored Docs and actual retrieval data; never embeds."""

from search_core.plugin_api.retrieval import IndexInspection


def vector_preview(vector):
    return {"dimension": len(vector), "preview": list(vector[:8])}


def content(point, filter_fields):
    payload = dict(point.payload)
    if isinstance(payload.get("labels"), dict):
        payload.update({f"labels.{k}": v for k, v in payload["labels"].items()})
    return {
        "id": point.id,
        "search_text": point.payload.get("search_text", ""),
        "filters": {
            key: payload[key] for key in sorted(filter_fields) if key in payload
        },
    }


def inspect_documents(documents, filter_fields):
    fields = set(filter_fields)
    for doc in documents:
        fields.update(doc.filters or doc.metadata.get("filters", {}))
    previews = []
    for doc in documents:
        point = doc.to_index_point(fields)
        previews.append(
            {
                **content(point, fields),
                "doc_version": doc.version,
                "deleted": doc.is_deleted,
                "vector": vector_preview(point.vector),
            }
        )
    return previews, fields


def inspect_index(index, source_id, fields):
    if not isinstance(index, IndexInspection):
        return {"status": "unsupported", "error": "INDEX_INSPECTION_UNSUPPORTED"}
    try:
        points = index.read_source_points(source_id)
        result = {"status": "ok", "error": ""}
        for kind in ("vector", "keyword"):
            result[kind + "_documents"] = [
                {
                    **content(point, fields),
                    "index_key": point.payload.get("index_key"),
                    **(
                        {"vector": vector_preview(point.vector)}
                        if kind == "vector"
                        else {}
                    ),
                }
                for point in points[kind]
            ]
        return result
    except Exception:  # noqa: BLE001 - expose diagnostic failure, preserve OB state
        # This optional diagnostic must not hide the independently readable OB state.
        return {"status": "unavailable", "error": "QDRANT_READ_FAILED"}
