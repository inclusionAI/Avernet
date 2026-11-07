"""Business-neutral document groups, embedding reuse and retirement."""

import hashlib
import math
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from search_core.catalog.cards import label_filters
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import IndexDocument
from search_core.plugin_api.vector_point import VectorPoint


def valid_embedding(vector, model):
    """Invalid cached vectors are misses; freshly generated vectors must validate."""
    return (
        bool(vector)
        and len(vector) == getattr(model, "dimension", len(vector))
        and all(math.isfinite(v) for v in vector)
    )


def reusable_document(previous, key, embedding_key, text_hash, model):
    """Find an exact input match even when a newer generation has different text."""
    return next(
        (
            d
            for d in previous
            if d.id == key
            and not d.is_deleted
            and d.metadata.get("embedding_key") == embedding_key
            and d.metadata.get("text_hash") == text_hash
            and valid_embedding(d.point.vector, model)
        ),
        None,
    )


def build_documents(row, model, embedding_key, filter_fields, converter, previous):
    """Project business text once, then materialize it without interpreting the Card."""
    if row.card.deleted:
        point = VectorPoint(
            id=row.card.agent_id, vector=[], payload={"source_id": row.card.agent_id}
        )
        main = IndexDocument(point, row.revision, {}, True, filters={})
        return complete_document_group(main, (), previous, model, embedding_key)
    projection = converter.convert(row.card)
    if not isinstance(projection, CardProjection):
        raise TypeError("converter must return CardProjection")
    expressions = tuple(projection.documents)
    if (
        not expressions
        or len(expressions) > 65
        or len({d.key for d in expressions}) != len(expressions)
    ):
        raise ValueError("converter must return 1-65 documents with unique keys")
    validate_labels(projection.filters, filter_fields)
    text = expressions[0].search_text
    digest = hashlib.sha256(text.encode()).hexdigest()
    old = reusable_document(previous, row.card.agent_id, embedding_key, digest, model)
    vector = list(old.point.vector) if old is not None else list(model.embed(text))
    point = VectorPoint(
        id=row.card.agent_id,
        vector=vector,
        payload={
            "source_id": row.card.agent_id,
            "source_version": row.card.source_version,
            "search_text": text,
            "rerank_text": expressions[0].rerank_text or text,
        },
    )
    main = IndexDocument(
        point,
        row.revision,
        {
            "embedding_key": embedding_key,
            "text_hash": digest,
            "document_key": expressions[0].key,
            "conversion_version": converter.version,
        },
        filters={**projection.filters, **label_filters(row.card.labels)},
    )
    return complete_document_group(main, expressions, previous, model, embedding_key)


def source_id(document):
    # Legacy artifacts used their Card ID as the point ID.
    return document.source_id


def load_group(store, source):
    documents = {d.id: d for d in store.load_by_source(source)}
    main = documents.get(source)
    if main is None:
        return []
    ids = main.metadata.get("document_ids", [main.id])
    if any(key not in documents for key in ids) or any(
        documents[key].source_revision != main.source_revision
        or source_id(documents[key]) != source
        for key in ids
    ):
        raise ValueError("incomplete or changing document group")
    return [documents[key] for key in ids]


def validate_labels(labels, filter_fields):
    """Validate builder output without interpreting business values."""
    if not isinstance(labels, dict) or set(labels) - set(filter_fields):
        raise ValueError("builder returned undeclared filter fields")
    for values in labels.values():
        if (
            not isinstance(values, tuple)
            or not values
            or any(not isinstance(v, str) or not v.strip() for v in values)
        ):
            raise ValueError("builder labels require nonempty tuples of strings")


def retired_documents(previous, current):
    ids = {d.id for d in current}
    revision = current[0].source_revision
    return [
        replace(d, source_revision=revision, is_deleted=True)
        for d in previous
        if d.id not in ids
    ]


def complete_document_group(main, expressions, previous, model, embedding_key):
    documents = [main]
    for expression in expressions[1:]:
        key = "doc:" + str(uuid5(NAMESPACE_URL, repr((main.source_id, expression.key))))
        digest = hashlib.sha256(expression.search_text.encode()).hexdigest()
        old = reusable_document(previous, key, embedding_key, digest, model)
        reusable = old is not None
        vector = old.point.vector if reusable else []
        if not main.is_deleted and not reusable:
            vector = list(model.embed(expression.search_text))
        point = VectorPoint(
            id=key,
            vector=list(vector),
            payload={
                **main.point.payload,
                "search_text": expression.search_text,
                "rerank_text": expression.rerank_text or expression.search_text,
            },
        )
        documents.append(
            IndexDocument(
                point,
                main.source_revision,
                {
                    "embedding_key": embedding_key,
                    "text_hash": digest,
                    "document_key": expression.key,
                },
                main.is_deleted,
                filters=main.filters,
            )
        )
    if not main.is_deleted:
        dimension = getattr(model, "dimension", len(main.point.vector))
        for document in documents:
            if (
                not valid_embedding(document.point.vector, model)
                or len(document.point.vector) != dimension
            ):
                raise ValueError("invalid document embedding")
    main.metadata["document_ids"] = [d.id for d in documents]
    return documents
