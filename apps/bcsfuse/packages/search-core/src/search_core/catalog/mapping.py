"""Apply configured projection rules without owning the business defaults."""

from search_core.config.document_mapping import MappingError, render
from search_core.plugin_api.card_documents import CardProjection
from search_core.plugin_api.index_documents import DocumentText


def map_projection(card, mapping, default, *, protected_filter_fields=()):
    protected = set(mapping.filter_fields) & set(protected_filter_fields)
    if protected:
        raise MappingError(
            ("filter_fields", min(protected)), "business filter is protected"
        )
    # An explicit search template defines one expression. Partial mappings keep
    # the business converter's existing fragment boundaries and stable keys.
    expressions = tuple(default.documents)
    if not expressions:
        expressions = (None,)
    if mapping.search_text_template is not None:
        expressions = expressions[:1]
    documents, warnings = [], []
    for expression in expressions:
        result = render(
            mapping,
            card,
            search_text=expression.search_text if expression else "",
            rerank_text=(
                expression.rerank_text
                if expression and mapping.search_text_template is None
                else ""
            ),
            filters=default.filters,
        )
        documents.append(
            DocumentText(
                expression.key if expression else "main",
                result.search_text,
                result.rerank_text,
            )
        )
        for warning in result.warnings:
            if warning not in warnings:
                warnings.append(warning)
    return CardProjection(tuple(documents), result.filters), tuple(warnings)
