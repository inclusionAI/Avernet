"""Translate trusted Card scope to source_id filters, never to an all-Agent list."""


class DocumentIndex:
    def __init__(self, index):
        self.index = index

    @staticmethod
    def filters(filters, allowed_ids):
        if "source_id" in filters:
            raise ValueError("source_id is reserved for trusted scope")
        return (
            dict(filters)
            if allowed_ids is None
            else {**filters, "source_id": tuple(allowed_ids)}
        )

    def vector_search(self, vector, limit, filters, allowed_ids):
        if allowed_ids == frozenset():
            return []
        return self.index.vector_search(
            vector, limit, self.filters(filters, allowed_ids), None
        )

    def keyword_search(self, query, limit, filters, allowed_ids):
        if allowed_ids == frozenset():
            return []
        return self.index.keyword_search(
            query, limit, self.filters(filters, allowed_ids), None
        )
